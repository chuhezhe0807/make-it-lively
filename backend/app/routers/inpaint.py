"""POST /api/inpaint — fill element holes via Replicate inpainting."""
from __future__ import annotations

import base64
import io
import urllib.request
from typing import Any, Final, cast

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException, status
from PIL import Image, ImageFilter
from pydantic import BaseModel, Field
from replicate.client import Client as ReplicateClient

from app import config, storage

INPAINT_MODEL: Final[str] = "stability-ai/stable-diffusion-inpainting"
INPAINT_PROMPT: Final[str] = (
    "clean background, photorealistic, seamless continuation of the scene"
)

IMAGE_MEDIA_TYPES: Final[dict[str, str]] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}

router = APIRouter(prefix="/api", tags=["inpaint"])


class Mask(BaseModel):
    bbox: list[float] = Field(..., min_length=4, max_length=4)
    # Optional precise contour from the segmentation step.  When present,
    # the inpaint mask is drawn from the contour polygon instead of the bbox.
    contour: list[list[float]] | None = None


class InpaintRequest(BaseModel):
    image_id: str = Field(..., min_length=1)
    masks: list[Mask]


class InpaintResponse(BaseModel):
    image_id: str
    background_url: str


def get_replicate_client() -> ReplicateClient:
    """Return a Replicate client; monkeypatched in tests."""
    return ReplicateClient()


def _find_image(image_id: str) -> tuple[bytes, str]:
    for ext, media_type in IMAGE_MEDIA_TYPES.items():
        path = storage.IMAGES_DIR / f"{image_id}.{ext}"
        if path.exists():
            return path.read_bytes(), media_type
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Image not found: {image_id}",
    )


def _fetch_url_bytes(url: str) -> bytes:
    with urllib.request.urlopen(url) as resp:  # noqa: S310 — URL from Replicate
        return cast(bytes, resp.read())


def _coerce_image_bytes(output: Any) -> bytes:
    """Normalize replicate.run's variable return shape into raw PNG bytes."""
    candidate: Any = output
    if isinstance(candidate, list) and candidate:
        candidate = candidate[0]
    if hasattr(candidate, "read"):
        data = candidate.read()
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
    if isinstance(candidate, (bytes, bytearray)):
        return bytes(candidate)
    if isinstance(candidate, str):
        return _fetch_url_bytes(candidate)
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="Unexpected inpainting output shape",
    )


def _draw_mask_regions(
    arr: np.ndarray,
    masks: list[Mask],
    canvas_w: int,
    canvas_h: int,
) -> None:
    """Fill *arr* in-place using contour polygons when available, else bboxes."""
    for m in masks:
        if m.contour and len(m.contour) >= 3:
            pts = np.array(m.contour, dtype=np.int32).reshape(-1, 1, 2)
            cv2.fillPoly(arr, [pts], color=255)
        else:
            x, y, w, h = m.bbox
            x1 = max(0, int(round(x)))
            y1 = max(0, int(round(y)))
            x2 = min(canvas_w, int(round(x + w)))
            y2 = min(canvas_h, int(round(y + h)))
            cv2.rectangle(arr, (x1, y1), (x2, y2), color=255, thickness=-1)


def _build_combined_mask(size: tuple[int, int], masks: list[Mask]) -> bytes:
    """Render a binary mask PNG using contour polygons (preferred) or bboxes.

    A small morphological dilation is applied to ensure the mask fully covers
    any residual fringe pixels around the element boundary.
    """
    # size is (width, height) in Pillow convention; numpy wants (rows, cols).
    arr = np.zeros((size[1], size[0]), dtype=np.uint8)
    _draw_mask_regions(arr, masks, canvas_w=size[0], canvas_h=size[1])

    # Dilate by ~4 px to cover sub-pixel fringe.
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    dilated: np.ndarray = cv2.dilate(arr, kernel, iterations=1)
    arr = dilated

    mask_img = Image.fromarray(arr, mode="L")
    buf = io.BytesIO()
    mask_img.save(buf, format="PNG")
    return buf.getvalue()


def _save_background(image_id: str, data: bytes, size: tuple[int, int]) -> None:
    layers_dir = storage.LAYERS_DIR / image_id
    layers_dir.mkdir(parents=True, exist_ok=True)
    img = Image.open(io.BytesIO(data)).convert("RGB")
    if img.size != size:
        img = img.resize(size, Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    (layers_dir / "background.png").write_bytes(buf.getvalue())


def _inpaint_with_fallback(
    image_bytes: bytes,
    masks: list[Mask],
    size: tuple[int, int],
) -> bytes:
    """Blur-fill fallback used when Replicate inpainting is unavailable.

    Strategy: Gaussian-blur the whole source image, then composite the
    blurred pixels onto the bbox regions of the original via a feathered
    mask. The result is not a true inpaint — the blurred smear still hints
    at the removed element — but it hides the hard silhouette well enough
    to sit underneath the foreground layers without breaking the illusion.
    """
    base = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    # Heavy blur fills the "hole" with softened surrounding pixels.
    blurred = base.filter(ImageFilter.GaussianBlur(radius=32))

    # Binary mask using contour polygons when available, else bboxes.
    arr = np.zeros((size[1], size[0]), dtype=np.uint8)
    _draw_mask_regions(arr, masks, canvas_w=size[0], canvas_h=size[1])
    mask_img = Image.fromarray(arr, mode="L")

    # Feather the mask edges so the blurred patch blends into the rest of
    # the background — a hard edge looks obviously fake.
    feathered = mask_img.filter(ImageFilter.GaussianBlur(radius=16))

    out = Image.composite(blurred, base, feathered)

    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


@router.post("/inpaint", response_model=InpaintResponse)
def inpaint_background(request: InpaintRequest) -> InpaintResponse:
    image_bytes, media_type = _find_image(request.image_id)
    base = Image.open(io.BytesIO(image_bytes))
    size = base.size

    if not request.masks:
        # No holes to fill — the original image is the background layer.
        _save_background(request.image_id, image_bytes, size)
        return InpaintResponse(
            image_id=request.image_id,
            background_url=f"/storage/layers/{request.image_id}/background.png",
        )

    if config.use_replicate_fallback():
        # Local Pillow fallback — no Replicate call.
        filled_bytes = _inpaint_with_fallback(image_bytes, request.masks, size)
        _save_background(request.image_id, filled_bytes, size)
        return InpaintResponse(
            image_id=request.image_id,
            background_url=f"/storage/layers/{request.image_id}/background.png",
        )

    # Real inpainting via Replicate SD inpainting model.
    mask_bytes = _build_combined_mask(size, request.masks)
    image_b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    mask_b64 = base64.standard_b64encode(mask_bytes).decode("ascii")
    image_data_uri = f"data:{media_type};base64,{image_b64}"
    mask_data_uri = f"data:image/png;base64,{mask_b64}"

    client = get_replicate_client()
    output = client.run(
        INPAINT_MODEL,
        input={
            "image": image_data_uri,
            "mask": mask_data_uri,
            "prompt": INPAINT_PROMPT,
        },
    )
    filled_bytes = _coerce_image_bytes(output)
    _save_background(request.image_id, filled_bytes, size)

    return InpaintResponse(
        image_id=request.image_id,
        background_url=f"/storage/layers/{request.image_id}/background.png",
    )
