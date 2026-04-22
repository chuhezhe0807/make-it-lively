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
    """Replicate client stub that records calls and returns canned output."""

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
    """Write a solid red PNG of the given size."""
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


def _sam2_auto_output(width: int = 32, height: int = 32) -> dict[str, Any]:
    """Mimic the dict returned by meta/sam-2 auto-segmentation.

    Returns a combined mask + one individual mask (fully white), matching
    the output schema: ``{combined_mask: <url>, individual_masks: [<url>]}``.
    """
    mask_bytes = _mask_png_bytes(width, height)
    return {
        "combined_mask": mask_bytes,
        "individual_masks": [mask_bytes],
    }


def _sample_elements() -> list[dict[str, Any]]:
    return [
        {"id": "cat", "label": "Orange cat", "bbox": [4.0, 8.0, 16.0, 16.0], "z_order": 2},
        {"id": "ball", "label": "Red ball", "bbox": [20.0, 20.0, 8.0, 8.0], "z_order": 1},
    ]


# ---------------------------------------------------------------------------
# SAM2 auto-segmentation path
# ---------------------------------------------------------------------------


def test_segment_success_writes_layers_and_returns_urls(
    isolated_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_id = "seg-ok"
    _write_png(storage.IMAGES_DIR / f"{image_id}.png")

    stub = _StubReplicateClient(output=_sam2_auto_output())
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

    # SAM2 should be called exactly once (auto-segmentation for the whole image).
    assert len(stub.calls) == 1
    first_call = stub.calls[0]
    assert first_call["ref"] == segment.SAM2_MODEL
    assert first_call["input"]["image"].startswith("data:image/png;base64,")


def test_segment_response_includes_contour_and_centroid(
    isolated_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Segment response layers should carry contour and centroid fields."""
    image_id = "seg-contour"
    _write_png(storage.IMAGES_DIR / f"{image_id}.png")

    stub = _StubReplicateClient(output=_sam2_auto_output())
    monkeypatch.setattr(segment, "get_replicate_client", lambda: stub)

    response = client.post(
        "/api/segment",
        json={"image_id": image_id, "elements": _sample_elements()[:1]},
    )

    assert response.status_code == 200
    layer = response.json()["layers"][0]
    # A fully-white mask produces a non-empty contour, centroid, and refined_bbox.
    assert layer["contour"] is not None
    assert isinstance(layer["contour"], list)
    assert len(layer["contour"]) >= 3
    assert layer["centroid"] is not None
    assert len(layer["centroid"]) == 2
    assert layer["refined_bbox"] is not None
    assert len(layer["refined_bbox"]) == 4


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

    mask_bytes = _mask_png_bytes()
    # SAM2 auto-segmentation returns dict; individual mask items are file-like.
    sam2_out = {
        "combined_mask": _FileOutput(mask_bytes),
        "individual_masks": [_FileOutput(mask_bytes)],
    }
    stub = _StubReplicateClient(output=sam2_out)
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
    stub = _StubReplicateClient(output=_sam2_auto_output())
    monkeypatch.setattr(segment, "get_replicate_client", lambda: stub)

    response = client.post(
        "/api/segment",
        json={"image_id": "nope", "elements": _sample_elements()},
    )

    assert response.status_code == 404
    assert stub.calls == []


def test_segment_empty_elements_returns_empty_layers(
    isolated_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_id = "seg-empty"
    _write_png(storage.IMAGES_DIR / f"{image_id}.png")

    stub = _StubReplicateClient(output=_sam2_auto_output())
    monkeypatch.setattr(segment, "get_replicate_client", lambda: stub)

    response = client.post(
        "/api/segment",
        json={"image_id": image_id, "elements": []},
    )

    assert response.status_code == 200
    assert response.json() == {"image_id": image_id, "layers": []}
    # No SAM2 call should be made for zero elements.
    assert stub.calls == []


# ---------------------------------------------------------------------------
# GrabCut fallback path
# ---------------------------------------------------------------------------


def test_segment_uses_grabcut_fallback_when_replicate_disabled(
    isolated_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When USE_REPLICATE_FALLBACK is on, GrabCut is used instead of SAM2.

    GrabCut may not perfectly segment a solid-colour image, so we just verify
    the structural invariants: full-canvas RGBA, non-zero alpha inside bbox.
    """
    image_id = "seg-fallback"
    _write_png(storage.IMAGES_DIR / f"{image_id}.png", width=40, height=30)

    monkeypatch.setenv("USE_REPLICATE_FALLBACK", "true")
    stub = _StubReplicateClient(output=_sam2_auto_output())
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
    # Layer must match the full canvas size.
    assert layer.size == (40, 30)

    # GrabCut-produced layer: pixel outside bbox should be transparent.
    outside = layer.getpixel((0, 0))
    assert isinstance(outside, tuple) and outside[3] == 0

    # The response should also include contour/centroid from GrabCut mask.
    body = response.json()
    cat_layer = body["layers"][0]
    assert "contour" in cat_layer
    assert "centroid" in cat_layer


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
