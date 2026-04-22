"""POST /api/segment — cut transparent PNG layers via Replicate SAM2 or local GrabCut."""
from __future__ import annotations

import base64
import io
import logging
import urllib.request
from typing import Any, Final, cast

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException, status
from PIL import Image, ImageChops
from pydantic import BaseModel, Field
from replicate.client import Client as ReplicateClient

from app import config, storage
from app.services.contour import (
    compute_centroid,
    compute_tight_bbox,
    extract_contour,
    feather_mask,
)

logger = logging.getLogger(__name__)

SAM2_MODEL: Final[str] = "meta/sam-2"

IMAGE_MEDIA_TYPES: Final[dict[str, str]] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}

router = APIRouter(prefix="/api", tags=["segment"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


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
    contour: list[list[float]] | None = None
    centroid: list[float] | None = None
    # Tight bbox derived from the mask contour — more accurate than the
    # VLM's estimate.  [x, y, width, height] in image pixel coords.
    refined_bbox: list[float] | None = None


class SegmentResponse(BaseModel):
    image_id: str
    layers: list[Layer]


# ---------------------------------------------------------------------------
# Replicate helpers
# ---------------------------------------------------------------------------


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
    with urllib.request.urlopen(url) as resp:  # noqa: S310 — URL returned by Replicate API
        return cast(bytes, resp.read())


def _coerce_mask_bytes(item: Any) -> bytes:
    """Turn a single Replicate output item into raw PNG bytes."""
    if hasattr(item, "read"):
        data = item.read()
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
    if isinstance(item, (bytes, bytearray)):
        return bytes(item)
    if isinstance(item, str):
        return _fetch_url_bytes(item)
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="Unexpected SAM2 output shape",
    )


# ---------------------------------------------------------------------------
# SAM2 auto-segmentation: one call per image → all masks → bbox matching
# ---------------------------------------------------------------------------


def _run_sam2_auto(
    client: ReplicateClient,
    image_data_uri: str,
) -> list[Image.Image]:
    """Call SAM2 automatic segmentation and return all individual masks.

    ``meta/sam-2`` on Replicate performs full-image automatic mask generation.
    It returns ``{"combined_mask": ..., "individual_masks": [...]}``.  We
    parse each individual mask into an L-mode PIL Image.
    """
    output = client.run(
        SAM2_MODEL,
        input={
            "image": image_data_uri,
            "use_m2m": True,
        },
    )

    # Replicate output is a dict-like with "combined_mask" and "individual_masks".
    # Depending on the SDK version, it may be a dict, a Prediction, or
    # a namespace object.  Handle each gracefully.
    individual_raw: list[Any] = []
    if isinstance(output, dict):
        individual_raw = output.get("individual_masks", [])
    elif hasattr(output, "individual_masks"):
        individual_raw = list(output.individual_masks)
    elif isinstance(output, list):
        # Some versions return a flat list of mask URLs.
        individual_raw = output
    else:
        logger.warning("SAM2 returned unexpected output type: %s", type(output))
        return []

    masks: list[Image.Image] = []
    for item in individual_raw:
        try:
            raw_bytes = _coerce_mask_bytes(item)
            mask = Image.open(io.BytesIO(raw_bytes)).convert("L")
            masks.append(mask)
        except Exception:
            logger.warning("Failed to parse one SAM2 individual mask, skipping")
            continue

    return masks


def _mask_bbox_overlap(mask: Image.Image, bbox: list[float]) -> float:
    """Fraction of the bbox area covered by non-zero mask pixels (0..1).

    Used to match SAM2's automatic masks to VLM-detected element bboxes.
    """
    x, y, w, h = bbox
    left = max(0, int(round(x)))
    top = max(0, int(round(y)))
    right = min(mask.width, int(round(x + w)))
    bottom = min(mask.height, int(round(y + h)))
    if right <= left or bottom <= top:
        return 0.0

    arr = np.array(mask, dtype=np.uint8)
    roi = arr[top:bottom, left:right]
    # Count non-zero pixels inside the bbox region.
    fg_count = int(np.count_nonzero(roi))
    bbox_area = (right - left) * (bottom - top)
    return fg_count / bbox_area if bbox_area > 0 else 0.0


def _match_masks_to_elements(
    masks: list[Image.Image],
    elements: list[Element],
    canvas_size: tuple[int, int],
) -> dict[str, Image.Image]:
    """For each element, pick (and merge) the best-matching SAM2 masks.

    Strategy:
    1. For every (mask, element) pair compute the overlap ratio.
    2. For each element, collect masks whose overlap exceeds a threshold.
    3. Merge collected masks with pixel-wise max (``ImageChops.lighter``).
    4. If no mask exceeds the threshold, fall back to the combined mask
       cropped to the bbox (rectangular, same as the old fallback).
    """
    OVERLAP_THRESHOLD = 0.15  # minimum overlap to consider a mask relevant

    matched: dict[str, Image.Image] = {}
    for element in elements:
        candidates: list[tuple[float, Image.Image]] = []
        for mask in masks:
            # Resize mask to canvas if needed (SAM2 may return different dims).
            if mask.size != canvas_size:
                mask = mask.resize(canvas_size, Image.Resampling.LANCZOS)
            overlap = _mask_bbox_overlap(mask, element.bbox)
            if overlap >= OVERLAP_THRESHOLD:
                candidates.append((overlap, mask))

        if candidates:
            # Sort by overlap descending and merge all qualifying masks.
            candidates.sort(key=lambda t: t[0], reverse=True)
            merged = candidates[0][1].copy()
            for _, extra in candidates[1:]:
                merged = ImageChops.lighter(merged, extra)
            matched[element.id] = merged
        else:
            # No SAM2 mask matched — create a rectangular fallback mask.
            fallback = Image.new("L", canvas_size, color=0)
            x, y, w, h = element.bbox
            arr = np.array(fallback, dtype=np.uint8)
            x1 = max(0, int(round(x)))
            y1 = max(0, int(round(y)))
            x2 = min(canvas_size[0], int(round(x + w)))
            y2 = min(canvas_size[1], int(round(y + h)))
            arr[y1:y2, x1:x2] = 255
            matched[element.id] = Image.fromarray(arr, mode="L")

    return matched


# ---------------------------------------------------------------------------
# Layer compositing
# ---------------------------------------------------------------------------


def _build_rgba_layer(image_bytes: bytes, mask: Image.Image) -> bytes:
    """Apply *mask* as the alpha channel of *image_bytes* (with feathering)."""
    base = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    if mask.size != base.size:
        mask = mask.resize(base.size, Image.Resampling.LANCZOS)
    feathered = feather_mask(mask)
    base.putalpha(feathered)
    buf = io.BytesIO()
    base.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# GrabCut local fallback (replaces the old rectangular-crop fallback)
# ---------------------------------------------------------------------------

# Maximum working dimension for GrabCut; larger images are downscaled first
# to keep processing time reasonable (≤ 5 s per element).
_GRABCUT_MAX_DIM: Final[int] = 1024


def _run_grabcut_once(
    img_bgr: np.ndarray,
    rect: tuple[int, int, int, int],
    n_iters: int,
) -> np.ndarray:
    """Single-pass GrabCut returning a binary foreground mask (0 / 255)."""
    bgd_model = np.zeros((1, 65), np.float64)
    fgd_model = np.zeros((1, 65), np.float64)
    gc_mask = np.zeros(img_bgr.shape[:2], np.uint8)

    try:
        cv2.grabCut(
            img_bgr, gc_mask, rect, bgd_model, fgd_model, n_iters, cv2.GC_INIT_WITH_RECT
        )
    except cv2.error:
        logger.warning("GrabCut failed for rect %s, using rectangular fallback", rect)
        gc_mask[rect[1] : rect[1] + rect[3], rect[0] : rect[0] + rect[2]] = cv2.GC_FGD

    fg = np.where(
        (gc_mask == cv2.GC_FGD) | (gc_mask == cv2.GC_PR_FGD), 255, 0
    ).astype(np.uint8)
    return fg


def _grabcut_segment(
    image_bytes: bytes,
    bbox: list[float],
    canvas_size: tuple[int, int],
) -> tuple[bytes, Image.Image]:
    """GrabCut-based local segmentation with optional two-pass refinement.

    Pass 1: run GrabCut with the VLM bbox.
    Pass 2 (when enabled): compute a tight bbox from the pass-1 contour
    and re-run GrabCut with the tighter rect for better accuracy.

    Returns ``(layer_png_bytes, raw_mask)`` where *raw_mask* is the
    pre-feathering L-mode mask suitable for contour extraction.
    """
    x, y, w, h = bbox
    left = max(0, int(round(x)))
    top = max(0, int(round(y)))
    right = min(canvas_size[0], int(round(x + w)))
    bottom = min(canvas_size[1], int(round(y + h)))
    if right <= left or bottom <= top:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"bbox {bbox} has no overlap with the image bounds",
        )

    base_pil = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    # Optionally downscale for performance on large images.
    scale = 1.0
    work_pil = base_pil
    if canvas_size[0] > _GRABCUT_MAX_DIM or canvas_size[1] > _GRABCUT_MAX_DIM:
        scale = min(_GRABCUT_MAX_DIM / canvas_size[0], _GRABCUT_MAX_DIM / canvas_size[1])
        new_w = int(canvas_size[0] * scale)
        new_h = int(canvas_size[1] * scale)
        work_pil = base_pil.resize((new_w, new_h), Image.Resampling.LANCZOS)

    img_bgr = cv2.cvtColor(np.array(work_pil), cv2.COLOR_RGB2BGR)
    n_iters = config.grabcut_iterations()

    # Scale bbox to working resolution.
    rect = (
        max(0, int(left * scale)),
        max(0, int(top * scale)),
        max(1, int((right - left) * scale)),
        max(1, int((bottom - top) * scale)),
    )

    # --- Pass 1 ---
    fg = _run_grabcut_once(img_bgr, rect, n_iters)

    # --- Pass 2 (optional): refine with tight bbox from pass-1 contour ---
    if config.grabcut_two_pass():
        bbox_area = rect[2] * rect[3]
        fg_area = int(np.count_nonzero(fg))
        # Only attempt pass 2 if pass 1 produced a meaningful mask.
        if bbox_area > 0 and fg_area / bbox_area >= 0.05:
            mask_pil_pass1 = Image.fromarray(fg, mode="L")
            contour_pts = extract_contour(mask_pil_pass1)
            tight = compute_tight_bbox(contour_pts) if contour_pts else None
            if tight is not None:
                tx, ty, tw, th = tight
                # Add a small padding (3px at working resolution) for safety.
                pad = 3
                refined_rect = (
                    max(0, int(tx) - pad),
                    max(0, int(ty) - pad),
                    min(img_bgr.shape[1] - max(0, int(tx) - pad), int(tw) + 2 * pad),
                    min(img_bgr.shape[0] - max(0, int(ty) - pad), int(th) + 2 * pad),
                )
                if refined_rect[2] > 0 and refined_rect[3] > 0:
                    fg = _run_grabcut_once(img_bgr, refined_rect, n_iters)

    raw_mask = Image.fromarray(fg, mode="L")
    # Scale mask back to original canvas dimensions.
    if scale != 1.0:
        raw_mask = raw_mask.resize(canvas_size, Image.Resampling.LANCZOS)

    # Build the RGBA layer with feathering.
    layer_bytes = _build_rgba_layer(image_bytes, raw_mask)
    return layer_bytes, raw_mask


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post("/segment", response_model=SegmentResponse)
def segment_elements(request: SegmentRequest) -> SegmentResponse:
    image_bytes, media_type = _find_image(request.image_id)

    # Capture the canvas size once so both code paths use the same value.
    canvas_size = Image.open(io.BytesIO(image_bytes)).size

    layers_dir = storage.LAYERS_DIR / request.image_id
    layers_dir.mkdir(parents=True, exist_ok=True)

    use_fallback = config.use_replicate_fallback()

    # ---- SAM2 path: one auto-segmentation call, then bbox matching --------
    element_masks: dict[str, Image.Image] = {}
    if not use_fallback and request.elements:
        client = get_replicate_client()
        image_b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        image_data_uri = f"data:{media_type};base64,{image_b64}"

        all_masks = _run_sam2_auto(client, image_data_uri)
        element_masks = _match_masks_to_elements(all_masks, request.elements, canvas_size)

    # ---- Build layers for each element ------------------------------------
    layers: list[Layer] = []
    for element in request.elements:
        raw_mask: Image.Image | None = None

        if use_fallback:
            layer_bytes, raw_mask = _grabcut_segment(image_bytes, element.bbox, canvas_size)
        else:
            mask = element_masks.get(element.id)
            if mask is None:
                # Should not happen (match always produces a fallback), but
                # defend with a blank mask.
                mask = Image.new("L", canvas_size, color=0)
            raw_mask = mask
            layer_bytes = _build_rgba_layer(image_bytes, mask)

        # Extract contour, centroid, and tight bbox from the pre-feathering mask.
        contour: list[list[float]] | None = None
        centroid: list[float] | None = None
        refined_bbox: list[float] | None = None
        contour_pts = extract_contour(raw_mask)
        if contour_pts:
            contour = contour_pts
            centroid = compute_centroid(contour_pts)
            refined_bbox = compute_tight_bbox(contour_pts)

        layer_path = layers_dir / f"{element.id}.png"
        layer_path.write_bytes(layer_bytes)
        layers.append(
            Layer(
                element_id=element.id,
                url=f"/storage/layers/{request.image_id}/{element.id}.png",
                contour=contour,
                centroid=centroid,
                refined_bbox=refined_bbox,
            )
        )

    return SegmentResponse(image_id=request.image_id, layers=layers)
