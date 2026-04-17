"""Tests for the /api/upload endpoint."""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import storage
from app.main import app
from app.routers import upload

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect every upload to a throwaway directory per test."""
    images_dir = tmp_path / "images"
    monkeypatch.setattr(storage, "IMAGES_DIR", images_dir)
    return images_dir


def _png_bytes(width: int = 32, height: int = 24, color: str = "red") -> bytes:
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes(width: int = 16, height: int = 16) -> bytes:
    img = Image.new("RGB", (width, height), color="blue")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _webp_bytes(width: int = 8, height: int = 8) -> bytes:
    img = Image.new("RGB", (width, height), color="green")
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    return buf.getvalue()


def _gif_bytes(width: int = 8, height: int = 8) -> bytes:
    img = Image.new("P", (width, height))
    buf = io.BytesIO()
    img.save(buf, format="GIF")
    return buf.getvalue()


def test_upload_png_success(_isolated_storage: Path) -> None:
    payload = _png_bytes(width=64, height=48)
    response = client.post(
        "/api/upload",
        files={"file": ("sample.png", payload, "image/png")},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["width"] == 64
    assert body["height"] == 48
    image_id = body["image_id"]
    saved = _isolated_storage / f"{image_id}.png"
    assert saved.exists()
    assert saved.read_bytes() == payload


def test_upload_jpeg_success(_isolated_storage: Path) -> None:
    payload = _jpeg_bytes()
    response = client.post(
        "/api/upload",
        files={"file": ("sample.jpg", payload, "image/jpeg")},
    )
    assert response.status_code == 201
    image_id = response.json()["image_id"]
    assert (_isolated_storage / f"{image_id}.jpg").exists()


def test_upload_webp_success(_isolated_storage: Path) -> None:
    payload = _webp_bytes()
    response = client.post(
        "/api/upload",
        files={"file": ("sample.webp", payload, "image/webp")},
    )
    assert response.status_code == 201
    image_id = response.json()["image_id"]
    assert (_isolated_storage / f"{image_id}.webp").exists()


def test_upload_rejects_oversized_file(
    monkeypatch: pytest.MonkeyPatch, _isolated_storage: Path
) -> None:
    # Shrink the limit so the test payload stays tiny.
    monkeypatch.setattr(upload, "MAX_FILE_SIZE_BYTES", 128)
    payload = _png_bytes(width=128, height=128)  # well over 128 bytes
    assert len(payload) > 128
    response = client.post(
        "/api/upload",
        files={"file": ("big.png", payload, "image/png")},
    )
    assert response.status_code == 400
    assert "10MB" in response.json()["detail"] or "limit" in response.json()["detail"].lower()
    assert list(_isolated_storage.glob("*")) == []


def test_upload_rejects_invalid_format_bytes(_isolated_storage: Path) -> None:
    response = client.post(
        "/api/upload",
        files={"file": ("notes.txt", b"not an image at all", "text/plain")},
    )
    assert response.status_code == 400
    assert "Unsupported" in response.json()["detail"]
    assert list(_isolated_storage.glob("*")) == []


def test_upload_rejects_unsupported_image_format(_isolated_storage: Path) -> None:
    response = client.post(
        "/api/upload",
        files={"file": ("sample.gif", _gif_bytes(), "image/gif")},
    )
    assert response.status_code == 400
    assert "GIF" in response.json()["detail"]
    assert list(_isolated_storage.glob("*")) == []
