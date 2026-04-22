"""End-to-end test for articulated sub-part animation.

Verifies the data contract: given a properly decomposed perception result
(cat → head / body / front_legs / hind_legs / tail), the planner should
produce rotate-based leg animations rather than whole-body translate bouncing.

Both VLM responses are mocked via checked-in fixture files — no real API
calls are made.
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
from app.routers import perception, plan_animation, segment

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((FIXTURES_DIR / name).read_text()))


def _cat_image_bytes() -> bytes:
    """A simple synthetic image large enough to host the cat bboxes."""
    img = Image.new("RGB", (400, 450), color="skyblue")
    # Paint a dark rectangle where the cat lives.
    for y in range(230, 410):
        for x in range(18, 218):
            img.putpixel((x, y), (20, 20, 20))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _solid_mask_bytes(w: int = 400, h: int = 450) -> bytes:
    mask = Image.new("L", (w, h), color=255)
    buf = io.BytesIO()
    mask.save(buf, format="PNG")
    return buf.getvalue()


# -- Stubs for Anthropic + Replicate --


class _ToolBlock:
    type = "tool_use"

    def __init__(self, tool_input: dict[str, Any], name: str) -> None:
        self.input = tool_input
        self.name = name
        self.id = "toolu_cat"


class _Msg:
    def __init__(self, content: list[Any]) -> None:
        self.content = content
        self.stop_reason = "tool_use"


class _MsgFactory:
    def __init__(self, msg: _Msg) -> None:
        self._msg = msg
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _Msg:
        self.calls.append(kwargs)
        return self._msg


class _AnthropicStub:
    def __init__(self, msg: _Msg) -> None:
        self.messages = _MsgFactory(msg)


class _ReplicateStub:
    def __init__(self, output: Any) -> None:
        self._output = output
        self.calls: list[dict[str, Any]] = []

    def run(self, ref: str, input: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        self.calls.append({"ref": ref, "input": input or {}, "kwargs": kwargs})
        return self._output


# -- Fixtures --


@pytest.fixture
def isolated_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    images_dir = tmp_path / "images"
    layers_dir = tmp_path / "layers"
    perception_dir = tmp_path / "perception"
    images_dir.mkdir()
    monkeypatch.setattr(storage, "IMAGES_DIR", images_dir)
    monkeypatch.setattr(storage, "LAYERS_DIR", layers_dir)
    monkeypatch.setattr(storage, "PERCEPTION_DIR", perception_dir)
    monkeypatch.setenv("REPLICATE_API_TOKEN", "test-token")
    monkeypatch.delenv("USE_REPLICATE_FALLBACK", raising=False)
    return tmp_path


# -- Test --


def test_articulated_cat_produces_rotate_leg_animations(
    isolated_storage: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full chain: upload → perception → segment → plan-animation.

    Asserts that the planner DSL uses rotate (not translate) on the cat's
    leg sub-parts, with non-null pivots and loop=true for continuous motion.
    """
    http = TestClient(app)
    perception_fixture = _load_fixture("articulated_perception.json")
    plan_fixture = _load_fixture("articulated_plan.json")

    # 1. Upload
    upload_resp = http.post(
        "/api/upload",
        files={"file": ("cat.png", _cat_image_bytes(), "image/png")},
    )
    assert upload_resp.status_code == 201
    image_id = upload_resp.json()["image_id"]

    # 2. Perception — mock returns decomposed cat
    perception_stub = _AnthropicStub(
        _Msg([_ToolBlock(perception_fixture, perception.TOOL_NAME)])
    )
    monkeypatch.setattr(perception, "get_anthropic_client", lambda: perception_stub)

    perc_resp = http.post("/api/perception", json={"image_id": image_id})
    assert perc_resp.status_code == 200
    elements = perc_resp.json()["elements"]

    # Verify sub-parts were emitted.
    child_ids = [e["id"] for e in elements if e.get("parent_id") == "black_cat"]
    assert "black_cat.front_legs" in child_ids
    assert "black_cat.hind_legs" in child_ids

    # 3. Segment — mock SAM2 auto-segmentation with a solid mask
    sam2_output = {
        "combined_mask": _solid_mask_bytes(),
        "individual_masks": [_solid_mask_bytes()],
    }
    seg_stub = _ReplicateStub(output=sam2_output)
    monkeypatch.setattr(segment, "get_replicate_client", lambda: seg_stub)

    seg_resp = http.post(
        "/api/segment",
        json={"image_id": image_id, "elements": elements},
    )
    assert seg_resp.status_code == 200

    # 4. Plan animation — mock returns articulated rotate plan
    plan_stub = _AnthropicStub(
        _Msg([_ToolBlock(plan_fixture, plan_animation.TOOL_NAME)])
    )
    monkeypatch.setattr(plan_animation, "get_anthropic_client", lambda: plan_stub)

    plan_resp = http.post(
        "/api/plan-animation",
        json={
            "image_id": image_id,
            "elements": elements,
            "prompt": "让小猫原地奔跑",
        },
    )
    assert plan_resp.status_code == 200
    plan = plan_resp.json()["plan"]

    # -- Assertions on the DSL shape --

    animated_ids = {item["element_id"] for item in plan}

    # Legs must be animated.
    assert "black_cat.front_legs" in animated_ids
    assert "black_cat.hind_legs" in animated_ids

    # Each leg uses rotate (not translate) with a non-null pivot and loop.
    for item in plan:
        if item["element_id"] in ("black_cat.front_legs", "black_cat.hind_legs"):
            assert item["loop"] is True, f"{item['element_id']} should loop"
            for step in item["timeline"]:
                assert step["type"] == "rotate", (
                    f"{item['element_id']} should use rotate, got {step['type']}"
                )
                assert step["pivot"] is not None, (
                    f"{item['element_id']} rotate step should have a pivot"
                )

    # The parent black_cat should NOT have a large translate dy (no bouncing).
    parent_items = [i for i in plan if i["element_id"] == "black_cat"]
    for parent in parent_items:
        for step in parent["timeline"]:
            if step["type"] == "translate":
                dy = abs(step.get("dy") or 0)
                assert dy < 30, (
                    f"Parent cat should not bounce — got translate dy={dy}"
                )
