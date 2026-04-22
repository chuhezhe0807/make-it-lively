"""End-to-end backend pipeline test with mocked external services.

Exercises upload -> perception -> segment -> inpaint -> plan-animation in a
single test, using checked-in snapshot fixtures for Anthropic + Replicate
responses. Complements the manual real-API checklist in ``smoke.md``.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import storage
from app.main import app
from app.routers import inpaint, perception, plan_animation, segment

FIXTURES_DIR = Path(__file__).parent / "fixtures"
REFERENCE_WIDTH = 128
REFERENCE_HEIGHT = 128


def _reference_image_bytes() -> bytes:
    """Deterministic reference image — matches the smoke.md checklist."""
    img = Image.new("RGB", (REFERENCE_WIDTH, REFERENCE_HEIGHT), color="white")
    for y in range(12, 52):
        for x in range(8, 56):
            img.putpixel((x, y), (255, 150, 60))
    for y in range(80, 100):
        for x in range(70, 90):
            img.putpixel((x, y), (220, 40, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _load_snapshot(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((FIXTURES_DIR / name).read_text()))


def _solid_mask_bytes(width: int, height: int) -> bytes:
    mask = Image.new("L", (width, height), color=255)
    buf = io.BytesIO()
    mask.save(buf, format="PNG")
    return buf.getvalue()


def _solid_background_bytes(width: int, height: int) -> bytes:
    img = Image.new("RGB", (width, height), color=(128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _ToolUseBlock:
    type = "tool_use"

    def __init__(self, tool_input: dict[str, Any], name: str) -> None:
        self.input = tool_input
        self.name = name
        self.id = "toolu_e2e"


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


class _StubAnthropic:
    def __init__(self, message: _StubMessage) -> None:
        self.messages = _StubMessages(message)


class _StubReplicate:
    def __init__(self, output: Any) -> None:
        self._output = output
        self.calls: list[dict[str, Any]] = []

    def run(
        self, ref: str, input: dict[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        self.calls.append({"ref": ref, "input": input or {}, "kwargs": kwargs})
        return self._output


@pytest.fixture
def isolated_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    images_dir = tmp_path / "images"
    layers_dir = tmp_path / "layers"
    perception_dir = tmp_path / "perception"
    images_dir.mkdir()
    monkeypatch.setattr(storage, "IMAGES_DIR", images_dir)
    monkeypatch.setattr(storage, "LAYERS_DIR", layers_dir)
    monkeypatch.setattr(storage, "PERCEPTION_DIR", perception_dir)
    # Force the Replicate code path regardless of the dev's .env so the
    # mocked stubs below are actually hit. Fallback coverage lives in the
    # per-router tests.
    monkeypatch.setenv("REPLICATE_API_TOKEN", "test-replicate-token")
    monkeypatch.delenv("USE_REPLICATE_FALLBACK", raising=False)
    return tmp_path


def test_full_pipeline_with_mocked_externals(
    isolated_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(app)
    perception_snapshot = _load_snapshot("perception.json")
    plan_snapshot = _load_snapshot("plan_animation.json")

    # 1. Upload the reference image.
    upload_response = client.post(
        "/api/upload",
        files={"file": ("reference.png", _reference_image_bytes(), "image/png")},
    )
    assert upload_response.status_code == 201
    upload_body = upload_response.json()
    image_id = upload_body["image_id"]
    assert upload_body["width"] == REFERENCE_WIDTH
    assert upload_body["height"] == REFERENCE_HEIGHT
    assert (storage.IMAGES_DIR / f"{image_id}.png").exists()

    # 2. Perception (Anthropic stubbed to return the snapshot).
    perception_stub = _StubAnthropic(
        _StubMessage([_ToolUseBlock(perception_snapshot, perception.TOOL_NAME)])
    )
    monkeypatch.setattr(
        perception, "get_anthropic_client", lambda: perception_stub
    )

    perception_response = client.post(
        "/api/perception", json={"image_id": image_id}
    )
    assert perception_response.status_code == 200
    elements = perception_response.json()["elements"]
    assert [e["id"] for e in elements] == ["cat", "ball"]
    assert (storage.PERCEPTION_DIR / f"{image_id}.json").exists()

    # 3. Segment (Replicate stubbed with SAM2 auto-segmentation output).
    #    SAM2 returns a dict with combined_mask + individual_masks. We provide
    #    one fully-opaque mask — it will match all element bboxes.
    sam2_output = {
        "combined_mask": _solid_mask_bytes(REFERENCE_WIDTH, REFERENCE_HEIGHT),
        "individual_masks": [
            _solid_mask_bytes(REFERENCE_WIDTH, REFERENCE_HEIGHT),
        ],
    }
    segment_stub = _StubReplicate(output=sam2_output)
    monkeypatch.setattr(segment, "get_replicate_client", lambda: segment_stub)

    segment_response = client.post(
        "/api/segment",
        json={"image_id": image_id, "elements": elements},
    )
    assert segment_response.status_code == 200
    layers = segment_response.json()["layers"]
    assert {layer["element_id"] for layer in layers} == {"cat", "ball"}
    for layer in layers:
        expected = storage.LAYERS_DIR / image_id / f"{layer['element_id']}.png"
        assert expected.exists()
        assert layer["url"] == f"/storage/layers/{image_id}/{layer['element_id']}.png"
    # SAM2 auto-segmentation: 1 call for the whole image (not N per element).
    assert len(segment_stub.calls) == 1

    # 4. Inpaint (Replicate stubbed with a solid grey background).
    inpaint_stub = _StubReplicate(
        output=_solid_background_bytes(REFERENCE_WIDTH, REFERENCE_HEIGHT)
    )
    monkeypatch.setattr(inpaint, "get_replicate_client", lambda: inpaint_stub)

    masks_payload = [{"bbox": e["bbox"]} for e in elements]
    inpaint_response = client.post(
        "/api/inpaint",
        json={"image_id": image_id, "masks": masks_payload},
    )
    assert inpaint_response.status_code == 200
    assert (storage.LAYERS_DIR / image_id / "background.png").exists()
    assert len(inpaint_stub.calls) == 1

    # 5. Plan animation (Anthropic stubbed to return the DSL snapshot).
    plan_stub = _StubAnthropic(
        _StubMessage([_ToolUseBlock(plan_snapshot, plan_animation.TOOL_NAME)])
    )
    monkeypatch.setattr(
        plan_animation, "get_anthropic_client", lambda: plan_stub
    )

    plan_response = client.post(
        "/api/plan-animation",
        json={
            "image_id": image_id,
            "elements": elements,
            "prompt": "the cat chases the ball and the ball bounces",
        },
    )
    assert plan_response.status_code == 200
    plan_body = plan_response.json()
    assert plan_body["image_id"] == image_id
    assert [item["element_id"] for item in plan_body["plan"]] == ["cat", "ball"]
    assert plan_body["plan"][0]["timeline"][0]["type"] == "translate"
    assert plan_body["plan"][1]["timeline"][0]["type"] == "path-follow"

    # Each stubbed external service fired exactly the expected number of times.
    assert len(perception_stub.messages.calls) == 1
    assert len(plan_stub.messages.calls) == 1
