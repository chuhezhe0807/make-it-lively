"""POST /api/segment — cut transparent PNG layers via Replicate SAM2."""
from __future__ import annotations

import base64
import io
import urllib.request
from typing import Any, Final, cast

from fastapi import APIRouter, HTTPException, status
from PIL import Image
from pydantic import BaseModel, Field
from replicate.client import Client as ReplicateClient

from app import config, storage

SAM2_MODEL: Final[str] = "meta/sam-2"

IMAGE_MEDIA_TYPES: Final[dict[str, str]] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}

router = APIRouter(prefix="/api", tags=["segment"])


class Element(BaseModel):
    id: str = Field(..., min_length=1)
    label: str
    bbox: list[float] = Field(..., min_length=4, max_length=4)
    z_order: int


class SegmentRequest(BaseModel):
    image_id: str = Field(..., min_length=1)
    elements: list[Element]


class Layer(BaseModel):
    element_id: str
    url: str


class SegmentResponse(BaseModel):
    image_id: str
    layers: list[Layer]


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
    with urllib.request.urlopen(url) as resp:  # noqa: S310 — URL is returned by Replicate API
        return cast(bytes, resp.read())


def _coerce_mask_bytes(output: Any) -> bytes:
    """Normalize replicate.run's variable return shape into raw mask PNG bytes."""
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
        detail="Unexpected SAM2 output shape",
    )


def _apply_mask(image_bytes: bytes, mask_bytes: bytes) -> bytes:
    base = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    mask = Image.open(io.BytesIO(mask_bytes)).convert("L")
    if mask.size != base.size:
        mask = mask.resize(base.size, Image.Resampling.LANCZOS)
    base.putalpha(mask)
    buf = io.BytesIO()
    base.save(buf, format="PNG")
    return buf.getvalue()


def _run_sam2(
    client: ReplicateClient,
    image_data_uri: str,
    bbox: list[float],
) -> bytes:
    x, y, w, h = bbox
    cx = int(x + w / 2)
    cy = int(y + h / 2)
    output = client.run(
        SAM2_MODEL,
        input={
            "image": image_data_uri,
            "click_coordinates": f"{cx},{cy}",
            "use_m2m": True,
        },
    )
    return _coerce_mask_bytes(output)


def _segment_with_fallback(
    image_bytes: bytes,
    bbox: list[float],
    canvas_size: tuple[int, int],
) -> bytes:
    """Rectangular-crop fallback used when Replicate SAM2 is unavailable.

    Strategy: build a fully-transparent canvas the same size as the source
    image and paste the pixels inside ``bbox`` at their original position.
    The resulting layer aligns correctly with the background under it, so
    the frontend's LayeredCanvas can stack it without any offset math. The
    trade-off versus SAM2 is a hard rectangular silhouette rather than a
    pixel-accurate mask.
    """
    x, y, w, h = bbox
    # Clamp to the canvas so slightly oversized VLM boxes don't crash crop().
    left = max(0, int(round(x)))
    top = max(0, int(round(y)))
    right = min(canvas_size[0], int(round(x + w)))
    bottom = min(canvas_size[1], int(round(y + h)))
    if right <= left or bottom <= top:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"bbox {bbox} has no overlap with the image bounds",
        )

    base = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    cropped = base.crop((left, top, right, bottom))

    # Transparent canvas at original size keeps per-layer coordinates aligned
    # with the background layer produced by /api/inpaint.
    layer = Image.new("RGBA", canvas_size, color=(0, 0, 0, 0))
    layer.paste(cropped, (left, top))

    buf = io.BytesIO()
    layer.save(buf, format="PNG")
    return buf.getvalue()


@router.post("/segment", response_model=SegmentResponse)
def segment_elements(request: SegmentRequest) -> SegmentResponse:
    image_bytes, media_type = _find_image(request.image_id)

    # Capture the canvas size once so both code paths use the same value.
    canvas_size = Image.open(io.BytesIO(image_bytes)).size

    layers_dir = storage.LAYERS_DIR / request.image_id
    layers_dir.mkdir(parents=True, exist_ok=True)

    # Two code paths: real SAM2 via Replicate, or a local Pillow fallback.
    # The fallback is picked when the caller has no Replicate token (or opts
    # in explicitly via USE_REPLICATE_FALLBACK=true).
    use_fallback = config.use_replicate_fallback()

    client: ReplicateClient | None = None
    image_data_uri: str | None = None
    if not use_fallback:
        client = get_replicate_client()
        image_b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        image_data_uri = f"data:{media_type};base64,{image_b64}"

    layers: list[Layer] = []
    for element in request.elements:
        if use_fallback:
            layer_bytes = _segment_with_fallback(image_bytes, element.bbox, canvas_size)
        else:
            # These locals were populated above when use_fallback is False;
            # the asserts narrow their types for mypy.
            assert client is not None  # noqa: S101 — narrow for mypy
            assert image_data_uri is not None  # noqa: S101 — narrow for mypy
            mask_bytes = _run_sam2(client, image_data_uri, element.bbox)
            layer_bytes = _apply_mask(image_bytes, mask_bytes)

        layer_path = layers_dir / f"{element.id}.png"
        layer_path.write_bytes(layer_bytes)
        layers.append(
            Layer(
                element_id=element.id,
                url=f"/storage/layers/{request.image_id}/{element.id}.png",
            )
        )

    return SegmentResponse(image_id=request.image_id, layers=layers)
