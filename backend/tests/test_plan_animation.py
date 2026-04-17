"""Tests for the /api/plan-animation endpoint."""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import plan_animation

client = TestClient(app)


class _StubToolUseBlock:
    type = "tool_use"

    def __init__(
        self,
        tool_input: dict[str, Any],
        name: str = plan_animation.TOOL_NAME,
    ) -> None:
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


def _sample_elements() -> list[dict[str, Any]]:
    return [
        {"id": "cat", "label": "Orange cat", "bbox": [10.0, 20.0, 30.0, 40.0], "z_order": 2},
        {"id": "ball", "label": "Red ball", "bbox": [50.0, 60.0, 15.0, 15.0], "z_order": 1},
    ]


def _sample_plan_payload() -> dict[str, Any]:
    return {
        "plan": [
            {
                "element_id": "cat",
                "timeline": [
                    {"type": "translate", "dx": 20.0, "dy": 0.0},
                    {"type": "rotate", "angle": 15.0},
                ],
                "easing": "power1.inOut",
                "loop": True,
                "duration_ms": 1200,
            },
            {
                "element_id": "ball",
                "timeline": [
                    {
                        "type": "path-follow",
                        "path": [[0.0, 0.0], [30.0, -20.0], [60.0, 0.0]],
                    },
                    {"type": "scale", "scale": 1.5},
                ],
                "easing": "sine.inOut",
                "loop": False,
                "duration_ms": 800,
            },
        ]
    }


def _post(elements: list[dict[str, Any]], prompt: str = "make them dance") -> Any:
    return client.post(
        "/api/plan-animation",
        json={"image_id": "img-xyz", "elements": elements, "prompt": prompt},
    )


def test_plan_animation_success_returns_dsl(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_client = _StubClient(_StubMessage([_StubToolUseBlock(_sample_plan_payload())]))
    monkeypatch.setattr(plan_animation, "get_anthropic_client", lambda: stub_client)

    response = _post(_sample_elements())

    assert response.status_code == 200
    body = response.json()
    assert body["image_id"] == "img-xyz"
    assert [e["element_id"] for e in body["plan"]] == ["cat", "ball"]

    cat = body["plan"][0]
    assert cat["easing"] == "power1.inOut"
    assert cat["loop"] is True
    assert cat["duration_ms"] == 1200
    assert cat["timeline"][0]["type"] == "translate"
    assert cat["timeline"][0]["dx"] == 20.0

    ball = body["plan"][1]
    assert ball["timeline"][0]["type"] == "path-follow"
    assert ball["timeline"][0]["path"] == [[0.0, 0.0], [30.0, -20.0], [60.0, 0.0]]

    assert len(stub_client.messages.calls) == 1
    call = stub_client.messages.calls[0]
    assert call["model"] == plan_animation.PLANNER_MODEL
    assert call["tool_choice"]["name"] == plan_animation.TOOL_NAME
    # Prompt + element listing both reach the LLM.
    user_text = "".join(
        block["text"] for block in call["messages"][0]["content"] if block["type"] == "text"
    )
    assert "make them dance" in user_text
    assert "cat" in user_text and "ball" in user_text


def test_plan_animation_rejects_unknown_element_id(monkeypatch: pytest.MonkeyPatch) -> None:
    bad_payload = {
        "plan": [
            {
                "element_id": "ghost",
                "timeline": [{"type": "opacity", "opacity": 0.0}],
                "easing": "power1.inOut",
                "loop": False,
                "duration_ms": 500,
            }
        ]
    }
    stub_client = _StubClient(_StubMessage([_StubToolUseBlock(bad_payload)]))
    monkeypatch.setattr(plan_animation, "get_anthropic_client", lambda: stub_client)

    response = _post(_sample_elements())

    assert response.status_code == 502
    assert "ghost" in response.json()["detail"]


def test_plan_animation_rejects_unstructured_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _TextBlock:
        type = "text"
        text = "I cannot use tools"

    stub_client = _StubClient(_StubMessage([_TextBlock()]))
    monkeypatch.setattr(plan_animation, "get_anthropic_client", lambda: stub_client)

    response = _post(_sample_elements())

    assert response.status_code == 502


def test_plan_animation_rejects_invalid_primitive(monkeypatch: pytest.MonkeyPatch) -> None:
    bad_payload = {
        "plan": [
            {
                "element_id": "cat",
                "timeline": [{"type": "teleport"}],  # not a supported primitive
                "easing": "power1.inOut",
                "loop": False,
                "duration_ms": 500,
            }
        ]
    }
    stub_client = _StubClient(_StubMessage([_StubToolUseBlock(bad_payload)]))
    monkeypatch.setattr(plan_animation, "get_anthropic_client", lambda: stub_client)

    response = _post(_sample_elements())

    assert response.status_code == 502
    assert "invalid DSL" in response.json()["detail"]


def test_plan_animation_requires_elements() -> None:
    response = client.post(
        "/api/plan-animation",
        json={"image_id": "img-xyz", "elements": [], "prompt": "hi"},
    )
    assert response.status_code == 422
