"""Tests for the /api/inpaint endpoint."""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import storage
from app.main import app
from app.routers import inpaint

client = TestClient(app)


class _StubReplicateClient:
    def __init__(self, output: Any) -> None:
        self._output = output
        self.calls: list[dict[str, Any]] = []

    def run(self, ref: str, input: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        self.calls.append({"ref": ref, "input": input or {}, "kwargs": kwargs})
        return self._output


@pytest.fixture
def isolated_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    images_dir = tmp_path / "images"
    layers_dir = tmp_path / "layers"
    images_dir.mkdir()
    monkeypatch.setattr(storage, "IMAGES_DIR", images_dir)
    monkeypatch.setattr(storage, "LAYERS_DIR", layers_dir)
    # Force the real Replicate code path regardless of the developer's .env:
    # tests that want the fallback toggle it explicitly.
    monkeypatch.setenv("REPLICATE_API_TOKEN", "test-replicate-token")
    monkeypatch.delenv("USE_REPLICATE_FALLBACK", raising=False)
    return tmp_path


def _write_png(path: Path, width: int = 32, height: int = 32) -> None:
    img = Image.new("RGB", (width, height), color="red")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    path.write_bytes(buf.getvalue())


def _filled_png_bytes(width: int = 32, height: int = 32) -> bytes:
    img = Image.new("RGB", (width, height), color="blue")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _sample_masks() -> list[dict[str, Any]]:
    return [
        {"bbox": [4.0, 8.0, 16.0, 16.0]},
        {"bbox": [20.0, 20.0, 8.0, 8.0]},
    ]


def _sample_masks_with_contours() -> list[dict[str, Any]]:
    """Masks that carry contour polygons from the segmentation step."""
    return [
        {
            "bbox": [4.0, 8.0, 16.0, 16.0],
            "contour": [[4, 8], [20, 8], [20, 24], [4, 24]],
        },
        {
            "bbox": [20.0, 20.0, 8.0, 8.0],
            "contour": [[20, 20], [28, 20], [28, 28], [20, 28]],
        },
    ]


def test_inpaint_success_writes_background_and_returns_url(
    isolated_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_id = "inp-ok"
    _write_png(storage.IMAGES_DIR / f"{image_id}.png")

    stub = _StubReplicateClient(output=_filled_png_bytes())
    monkeypatch.setattr(inpaint, "get_replicate_client", lambda: stub)

    response = client.post(
        "/api/inpaint",
        json={"image_id": image_id, "masks": _sample_masks()},
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "image_id": image_id,
        "background_url": f"/storage/layers/{image_id}/background.png",
    }

    bg_path = storage.LAYERS_DIR / image_id / "background.png"
    assert bg_path.exists()

    bg_img = Image.open(bg_path)
    assert bg_img.mode == "RGB"
    assert bg_img.size == (32, 32)

    assert len(stub.calls) == 1
    call = stub.calls[0]
    assert call["ref"] == inpaint.INPAINT_MODEL
    assert call["input"]["image"].startswith("data:image/png;base64,")
    assert call["input"]["mask"].startswith("data:image/png;base64,")


def test_inpaint_supports_file_like_replicate_output(
    isolated_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_id = "inp-file"
    _write_png(storage.IMAGES_DIR / f"{image_id}.png")

    class _FileOutput:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def read(self) -> bytes:
            return self._data

    stub = _StubReplicateClient(output=_FileOutput(_filled_png_bytes()))
    monkeypatch.setattr(inpaint, "get_replicate_client", lambda: stub)

    response = client.post(
        "/api/inpaint",
        json={"image_id": image_id, "masks": _sample_masks()[:1]},
    )

    assert response.status_code == 200
    assert (storage.LAYERS_DIR / image_id / "background.png").exists()


def test_inpaint_missing_image_returns_404(
    isolated_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _StubReplicateClient(output=_filled_png_bytes())
    monkeypatch.setattr(inpaint, "get_replicate_client", lambda: stub)

    response = client.post(
        "/api/inpaint",
        json={"image_id": "nope", "masks": _sample_masks()},
    )

    assert response.status_code == 404
    assert stub.calls == []


def test_inpaint_rejects_unexpected_replicate_output(
    isolated_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_id = "inp-bad"
    _write_png(storage.IMAGES_DIR / f"{image_id}.png")

    stub = _StubReplicateClient(output=12345)
    monkeypatch.setattr(inpaint, "get_replicate_client", lambda: stub)

    response = client.post(
        "/api/inpaint",
        json={"image_id": image_id, "masks": _sample_masks()[:1]},
    )

    assert response.status_code == 502
    assert not (storage.LAYERS_DIR / image_id / "background.png").exists()


def test_inpaint_empty_masks_copies_original_without_calling_replicate(
    isolated_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_id = "inp-empty"
    _write_png(storage.IMAGES_DIR / f"{image_id}.png")

    stub = _StubReplicateClient(output=_filled_png_bytes())
    monkeypatch.setattr(inpaint, "get_replicate_client", lambda: stub)

    response = client.post(
        "/api/inpaint",
        json={"image_id": image_id, "masks": []},
    )

    assert response.status_code == 200
    assert response.json() == {
        "image_id": image_id,
        "background_url": f"/storage/layers/{image_id}/background.png",
    }
    assert (storage.LAYERS_DIR / image_id / "background.png").exists()
    assert stub.calls == []


def test_inpaint_uses_pillow_fallback_when_replicate_disabled(
    isolated_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When USE_REPLICATE_FALLBACK is on, no Replicate call is made and the
    background is a Pillow-blur composite of the original."""
    image_id = "inp-fallback"
    _write_png(storage.IMAGES_DIR / f"{image_id}.png", width=40, height=30)

    monkeypatch.setenv("USE_REPLICATE_FALLBACK", "true")
    stub = _StubReplicateClient(output=_filled_png_bytes())
    monkeypatch.setattr(inpaint, "get_replicate_client", lambda: stub)

    response = client.post(
        "/api/inpaint",
        json={"image_id": image_id, "masks": _sample_masks()[:1]},
    )

    assert response.status_code == 200
    assert stub.calls == []  # Replicate never called.

    bg_path = storage.LAYERS_DIR / image_id / "background.png"
    assert bg_path.exists()
    bg_img = Image.open(bg_path)
    assert bg_img.mode == "RGB"
    assert bg_img.size == (40, 30)


def test_inpaint_accepts_masks_with_contours(
    isolated_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Masks carrying contour polygons should be accepted and produce a valid
    background (contour-based mask is used for inpainting)."""
    image_id = "inp-contour"
    _write_png(storage.IMAGES_DIR / f"{image_id}.png")

    stub = _StubReplicateClient(output=_filled_png_bytes())
    monkeypatch.setattr(inpaint, "get_replicate_client", lambda: stub)

    response = client.post(
        "/api/inpaint",
        json={"image_id": image_id, "masks": _sample_masks_with_contours()},
    )

    assert response.status_code == 200
    assert (storage.LAYERS_DIR / image_id / "background.png").exists()


def test_inpaint_fallback_accepts_masks_with_contours(
    isolated_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contour-based masks also work in the local blur-fill fallback path."""
    image_id = "inp-contour-fb"
    _write_png(storage.IMAGES_DIR / f"{image_id}.png", width=40, height=30)

    monkeypatch.setenv("USE_REPLICATE_FALLBACK", "true")
    stub = _StubReplicateClient(output=_filled_png_bytes())
    monkeypatch.setattr(inpaint, "get_replicate_client", lambda: stub)

    response = client.post(
        "/api/inpaint",
        json={"image_id": image_id, "masks": _sample_masks_with_contours()[:1]},
    )

    assert response.status_code == 200
    assert stub.calls == []
    bg_path = storage.LAYERS_DIR / image_id / "background.png"
    assert bg_path.exists()
    bg_img = Image.open(bg_path)
    assert bg_img.mode == "RGB"
    assert bg_img.size == (40, 30)
