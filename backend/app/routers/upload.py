"""POST /api/upload — accept an image and persist it to local storage."""
from __future__ import annotations

import io
import uuid
from typing import Annotated, Final

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel

from app import storage

MAX_FILE_SIZE_BYTES: Final[int] = 10 * 1024 * 1024

# Pillow format identifier -> filesystem extension.
ALLOWED_FORMATS: Final[dict[str, str]] = {
    "PNG": "png",
    "JPEG": "jpg",
    "WEBP": "webp",
}

router = APIRouter(prefix="/api", tags=["upload"])


class UploadResponse(BaseModel):
    image_id: str
    width: int
    height: int


@router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_image(file: Annotated[UploadFile, File(...)]) -> UploadResponse:
    contents = await file.read()

    if len(contents) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File exceeds 10MB limit",
        )

    try:
        with Image.open(io.BytesIO(contents)) as img:
            img_format = img.format
            width, height = img.size
    except UnidentifiedImageError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported image format",
        ) from exc

    if img_format not in ALLOWED_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported image format: {img_format}",
        )

    ext = ALLOWED_FORMATS[img_format]
    image_id = str(uuid.uuid4())
    storage.IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    (storage.IMAGES_DIR / f"{image_id}.{ext}").write_bytes(contents)

    return UploadResponse(image_id=image_id, width=width, height=height)
