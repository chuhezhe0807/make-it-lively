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

from app import storage

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


@router.post("/segment", response_model=SegmentResponse)
def segment_elements(request: SegmentRequest) -> SegmentResponse:
    image_bytes, media_type = _find_image(request.image_id)
    image_b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    image_data_uri = f"data:{media_type};base64,{image_b64}"

    layers_dir = storage.LAYERS_DIR / request.image_id
    layers_dir.mkdir(parents=True, exist_ok=True)

    client = get_replicate_client()
    layers: list[Layer] = []
    for element in request.elements:
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
