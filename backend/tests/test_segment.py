"""Tests for the /api/segment endpoint."""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import storage
from app.main import app
from app.routers import segment

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


def _mask_png_bytes(width: int = 32, height: int = 32) -> bytes:
    """A fully-opaque L-mode PNG that can act as an alpha mask."""
    mask = Image.new("L", (width, height), color=255)
    buf = io.BytesIO()
    mask.save(buf, format="PNG")
    return buf.getvalue()


def _sample_elements() -> list[dict[str, Any]]:
    return [
        {"id": "cat", "label": "Orange cat", "bbox": [4.0, 8.0, 16.0, 16.0], "z_order": 2},
        {"id": "ball", "label": "Red ball", "bbox": [20.0, 20.0, 8.0, 8.0], "z_order": 1},
    ]


def test_segment_success_writes_layers_and_returns_urls(
    isolated_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_id = "seg-ok"
    _write_png(storage.IMAGES_DIR / f"{image_id}.png")

    stub = _StubReplicateClient(output=_mask_png_bytes())
    monkeypatch.setattr(segment, "get_replicate_client", lambda: stub)

    response = client.post(
        "/api/segment",
        json={"image_id": image_id, "elements": _sample_elements()},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["image_id"] == image_id
    assert [layer["element_id"] for layer in body["layers"]] == ["cat", "ball"]
    for layer in body["layers"]:
        assert layer["url"] == f"/storage/layers/{image_id}/{layer['element_id']}.png"

    cat_path = storage.LAYERS_DIR / image_id / "cat.png"
    ball_path = storage.LAYERS_DIR / image_id / "ball.png"
    assert cat_path.exists()
    assert ball_path.exists()

    # Layers are RGBA PNGs.
    cat_layer = Image.open(cat_path)
    assert cat_layer.mode == "RGBA"
    assert cat_layer.size == (32, 32)

    assert len(stub.calls) == 2
    first_call = stub.calls[0]
    assert first_call["ref"] == segment.SAM2_MODEL
    assert first_call["input"]["image"].startswith("data:image/png;base64,")
    # bbox [4, 8, 16, 16] → centre (12, 16)
    assert first_call["input"]["click_coordinates"] == "12,16"


def test_segment_supports_file_like_replicate_output(
    isolated_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_id = "seg-file"
    _write_png(storage.IMAGES_DIR / f"{image_id}.png")

    class _FileOutput:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def read(self) -> bytes:
            return self._data

    stub = _StubReplicateClient(output=_FileOutput(_mask_png_bytes()))
    monkeypatch.setattr(segment, "get_replicate_client", lambda: stub)

    response = client.post(
        "/api/segment",
        json={"image_id": image_id, "elements": _sample_elements()[:1]},
    )

    assert response.status_code == 200
    assert (storage.LAYERS_DIR / image_id / "cat.png").exists()


def test_segment_missing_image_returns_404(
    isolated_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = _StubReplicateClient(output=_mask_png_bytes())
    monkeypatch.setattr(segment, "get_replicate_client", lambda: stub)

    response = client.post(
        "/api/segment",
        json={"image_id": "nope", "elements": _sample_elements()},
    )

    assert response.status_code == 404
    assert stub.calls == []


def test_segment_rejects_unexpected_replicate_output(
    isolated_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_id = "seg-bad"
    _write_png(storage.IMAGES_DIR / f"{image_id}.png")

    stub = _StubReplicateClient(output=12345)  # neither bytes, str, nor file-like
    monkeypatch.setattr(segment, "get_replicate_client", lambda: stub)

    response = client.post(
        "/api/segment",
        json={"image_id": image_id, "elements": _sample_elements()[:1]},
    )

    assert response.status_code == 502
    assert not (storage.LAYERS_DIR / image_id / "cat.png").exists()


def test_segment_empty_elements_returns_empty_layers(
    isolated_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_id = "seg-empty"
    _write_png(storage.IMAGES_DIR / f"{image_id}.png")

    stub = _StubReplicateClient(output=_mask_png_bytes())
    monkeypatch.setattr(segment, "get_replicate_client", lambda: stub)

    response = client.post(
        "/api/segment",
        json={"image_id": image_id, "elements": []},
    )

    assert response.status_code == 200
    assert response.json() == {"image_id": image_id, "layers": []}
    assert stub.calls == []


def test_segment_uses_pillow_fallback_when_replicate_disabled(
    isolated_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When USE_REPLICATE_FALLBACK is on, no Replicate call is made and the
    produced layer is a transparent canvas with the bbox region pasted in."""
    image_id = "seg-fallback"
    _write_png(storage.IMAGES_DIR / f"{image_id}.png", width=40, height=30)

    monkeypatch.setenv("USE_REPLICATE_FALLBACK", "true")
    # Even supplying a stub, the fallback path should NOT touch Replicate.
    stub = _StubReplicateClient(output=_mask_png_bytes())
    monkeypatch.setattr(segment, "get_replicate_client", lambda: stub)

    response = client.post(
        "/api/segment",
        json={
            "image_id": image_id,
            "elements": [
                {"id": "cat", "label": "Orange cat", "bbox": [5, 5, 10, 10], "z_order": 1}
            ],
        },
    )

    assert response.status_code == 200
    assert stub.calls == []  # Replicate never called.

    cat_path = storage.LAYERS_DIR / image_id / "cat.png"
    assert cat_path.exists()
    layer = Image.open(cat_path)
    assert layer.mode == "RGBA"
    # Layer must match the full canvas size so it aligns with the background.
    assert layer.size == (40, 30)
    # Pixel inside the bbox is fully opaque; outside is fully transparent.
    inside = layer.getpixel((6, 6))
    outside = layer.getpixel((0, 0))
    assert isinstance(inside, tuple) and inside[3] == 255
    assert isinstance(outside, tuple) and outside[3] == 0


def test_segment_fallback_rejects_bbox_outside_image(
    isolated_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_id = "seg-oob"
    _write_png(storage.IMAGES_DIR / f"{image_id}.png", width=32, height=32)

    monkeypatch.setenv("USE_REPLICATE_FALLBACK", "true")
    response = client.post(
        "/api/segment",
        json={
            "image_id": image_id,
            # Entire bbox lies to the right of the image.
            "elements": [
                {"id": "cat", "label": "Cat", "bbox": [100, 0, 10, 10], "z_order": 1}
            ],
        },
    )
    assert response.status_code == 422
