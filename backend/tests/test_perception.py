"""Tests for the /api/perception endpoint."""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import storage
from app.main import app
from app.routers import perception

client = TestClient(app)


class _StubToolUseBlock:
    type = "tool_use"

    def __init__(self, tool_input: dict[str, Any], name: str = perception.TOOL_NAME) -> None:
        self.input = tool_input
        self.name = name
        self.id = "toolu_test"


class _StubMessage:
    def __init__(self, content: list[Any]) -> None:
        self.content = content
        self.stop_reason = "tool_use"


class _StubMessages:
    def __init__(self, message: _StubMessage) -> None:
        self._message = message
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _StubMessage:
        self.calls.append(kwargs)
        return self._message


class _StubClient:
    def __init__(self, message: _StubMessage) -> None:
        self.messages = _StubMessages(message)


@pytest.fixture
def isolated_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    images_dir = tmp_path / "images"
    perception_dir = tmp_path / "perception"
    images_dir.mkdir()
    monkeypatch.setattr(storage, "IMAGES_DIR", images_dir)
    monkeypatch.setattr(storage, "PERCEPTION_DIR", perception_dir)
    return tmp_path


def _write_png(path: Path, width: int = 32, height: int = 32) -> None:
    img = Image.new("RGB", (width, height), color="red")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    path.write_bytes(buf.getvalue())


def _sample_elements_payload() -> dict[str, Any]:
    return {
        "elements": [
            {"id": "cat", "label": "Orange cat", "bbox": [10.0, 20.0, 30.0, 40.0], "z_order": 2},
            {"id": "ball", "label": "Red ball", "bbox": [50.0, 60.0, 15.0, 15.0], "z_order": 1},
        ]
    }


def test_perception_success_calls_vlm_and_caches(
    isolated_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_id = "abc123"
    _write_png(storage.IMAGES_DIR / f"{image_id}.png")

    stub_client = _StubClient(_StubMessage([_StubToolUseBlock(_sample_elements_payload())]))
    monkeypatch.setattr(perception, "get_anthropic_client", lambda: stub_client)

    response = client.post("/api/perception", json={"image_id": image_id})

    assert response.status_code == 200
    body = response.json()
    assert body["image_id"] == image_id
    assert len(body["elements"]) == 2
    assert body["elements"][0]["id"] == "cat"
    assert body["elements"][0]["bbox"] == [10.0, 20.0, 30.0, 40.0]

    cache_path = storage.PERCEPTION_DIR / f"{image_id}.json"
    assert cache_path.exists()
    cached = json.loads(cache_path.read_text())
    assert cached["elements"][0]["id"] == "cat"

    assert len(stub_client.messages.calls) == 1
    call = stub_client.messages.calls[0]
    assert call["model"] == perception.VLM_MODEL
    assert call["tool_choice"]["name"] == perception.TOOL_NAME


def test_perception_reads_from_cache_on_repeat(
    isolated_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_id = "cached1"
    _write_png(storage.IMAGES_DIR / f"{image_id}.png")

    stub_client = _StubClient(_StubMessage([_StubToolUseBlock(_sample_elements_payload())]))
    monkeypatch.setattr(perception, "get_anthropic_client", lambda: stub_client)

    first = client.post("/api/perception", json={"image_id": image_id})
    second = client.post("/api/perception", json={"image_id": image_id})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    # Only the first call should hit the VLM; second read from cache.
    assert len(stub_client.messages.calls) == 1


def test_perception_missing_image_returns_404(
    isolated_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_client = _StubClient(_StubMessage([_StubToolUseBlock(_sample_elements_payload())]))
    monkeypatch.setattr(perception, "get_anthropic_client", lambda: stub_client)

    response = client.post("/api/perception", json={"image_id": "nonexistent"})

    assert response.status_code == 404
    assert len(stub_client.messages.calls) == 0


def test_perception_preserves_parent_id_and_pivot(
    isolated_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Articulated sub-parts must round-trip parent_id + pivot to the cache."""
    image_id = "articulated"
    _write_png(storage.IMAGES_DIR / f"{image_id}.png")

    # Payload shaped like a robot decomposition: whole object + one arm child.
    payload = {
        "elements": [
            {
                "id": "blue_robot",
                "label": "Blue robot",
                "bbox": [100.0, 50.0, 200.0, 300.0],
                "z_order": 2,
            },
            {
                "id": "blue_robot.right_arm",
                "label": "Right arm",
                "bbox": [260.0, 120.0, 60.0, 120.0],
                "z_order": 3,
                "parent_id": "blue_robot",
                "pivot": [270.0, 130.0],
            },
        ]
    }
    stub_client = _StubClient(_StubMessage([_StubToolUseBlock(payload)]))
    monkeypatch.setattr(perception, "get_anthropic_client", lambda: stub_client)

    response = client.post("/api/perception", json={"image_id": image_id})

    assert response.status_code == 200
    body = response.json()
    # Parent stays atomic — no extra fields populated.
    assert body["elements"][0]["parent_id"] is None
    assert body["elements"][0]["pivot"] is None
    # Child carries through both fields.
    child = body["elements"][1]
    assert child["parent_id"] == "blue_robot"
    assert child["pivot"] == [270.0, 130.0]

    # Cache persists the new fields too.
    cached = json.loads((storage.PERCEPTION_DIR / f"{image_id}.json").read_text())
    assert cached["elements"][1]["parent_id"] == "blue_robot"
    assert cached["elements"][1]["pivot"] == [270.0, 130.0]


def test_perception_rejects_unstructured_response(
    isolated_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_id = "unstructured"
    _write_png(storage.IMAGES_DIR / f"{image_id}.png")

    class _TextBlock:
        type = "text"
        text = "I can't call tools"

    stub_client = _StubClient(_StubMessage([_TextBlock()]))
    monkeypatch.setattr(perception, "get_anthropic_client", lambda: stub_client)

    response = client.post("/api/perception", json={"image_id": image_id})

    assert response.status_code == 502
    assert not (storage.PERCEPTION_DIR / f"{image_id}.json").exists()


# ---------------------------------------------------------------------------
# _validate_sub_part_coverage
# ---------------------------------------------------------------------------


def _make_element(
    id: str,
    bbox: list[float],
    parent_id: str | None = None,
) -> perception.Element:
    return perception.Element(
        id=id, label=id, bbox=bbox, z_order=1, parent_id=parent_id
    )


def test_coverage_full_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Two children perfectly tile the parent → no warning."""
    elements = [
        _make_element("parent", [0, 0, 100, 100]),
        _make_element("parent.left", [0, 0, 50, 100], parent_id="parent"),
        _make_element("parent.right", [50, 0, 50, 100], parent_id="parent"),
    ]
    with caplog.at_level("WARNING"):
        perception._validate_sub_part_coverage(elements)
    assert "Sub-part coverage" not in caplog.text


def test_coverage_low_triggers_warning(caplog: pytest.LogCaptureFixture) -> None:
    """One tiny child on a large parent → warning."""
    elements = [
        _make_element("parent", [0, 0, 100, 100]),
        _make_element("parent.child", [0, 0, 30, 30], parent_id="parent"),
    ]
    with caplog.at_level("WARNING"):
        perception._validate_sub_part_coverage(elements)
    assert "Sub-part coverage" in caplog.text
    assert "parent" in caplog.text
