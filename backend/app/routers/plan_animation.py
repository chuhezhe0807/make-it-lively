"""POST /api/plan-animation — convert a natural-language prompt into a GSAP DSL."""
from __future__ import annotations

from typing import Any, Final, cast

import anthropic
from anthropic.types import MessageParam, ToolChoiceToolParam, ToolParam
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, ValidationError

from app.schemas.animation import (
    PRIMITIVE_TYPES,
    AnimationPlan,
    ElementAnimation,
)

PLANNER_MODEL: Final[str] = "claude-opus-4-7"
MAX_TOKENS: Final[int] = 16000
TOOL_NAME: Final[str] = "report_animation_plan"

PLANNER_PROMPT: Final[str] = (
    "You are planning a layered 2D animation. The user describes what they "
    "want to happen; you must return a GSAP-compatible plan for the listed "
    "elements. Use only the supplied element ids. For each element, produce a "
    "timeline built from these primitives: translate (dx, dy in pixels), "
    "rotate (angle in degrees), scale (factor, 1.0 = unchanged), opacity "
    "(0..1), path-follow (path: list of [x, y] points). Set sensible easing "
    "(e.g. 'power1.inOut', 'sine.inOut'), loop (true/false), and "
    "duration_ms (total duration for the element timeline). Call the "
    "report_animation_plan tool — do not respond with plain text."
)

PLANNER_TOOL: Final[dict[str, Any]] = {
    "name": TOOL_NAME,
    "description": "Report a per-element GSAP animation plan.",
    "input_schema": {
        "type": "object",
        "properties": {
            "plan": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "element_id": {"type": "string"},
                        "timeline": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "type": {
                                        "type": "string",
                                        "enum": list(PRIMITIVE_TYPES),
                                    },
                                    "dx": {"type": "number"},
                                    "dy": {"type": "number"},
                                    "angle": {"type": "number"},
                                    "scale": {"type": "number"},
                                    "opacity": {"type": "number"},
                                    "path": {
                                        "type": "array",
                                        "items": {
                                            "type": "array",
                                            "items": {"type": "number"},
                                            "minItems": 2,
                                            "maxItems": 2,
                                        },
                                    },
                                    "duration_ms": {"type": "integer"},
                                    "easing": {"type": "string"},
                                },
                                "required": ["type"],
                            },
                        },
                        "easing": {"type": "string"},
                        "loop": {"type": "boolean"},
                        "duration_ms": {"type": "integer"},
                    },
                    "required": [
                        "element_id",
                        "timeline",
                        "easing",
                        "loop",
                        "duration_ms",
                    ],
                },
            },
        },
        "required": ["plan"],
    },
}

router = APIRouter(prefix="/api", tags=["plan-animation"])


class ElementInput(BaseModel):
    id: str = Field(..., min_length=1)
    label: str
    bbox: list[float] = Field(..., min_length=4, max_length=4)
    z_order: int


class PlanAnimationRequest(BaseModel):
    image_id: str = Field(..., min_length=1)
    elements: list[ElementInput] = Field(..., min_length=1)
    prompt: str = Field(..., min_length=1)


def get_anthropic_client() -> anthropic.Anthropic:
    """Return an Anthropic client; monkeypatched in tests."""
    return anthropic.Anthropic()


def _extract_tool_input(message: Any) -> dict[str, Any]:
    for block in message.content:
        if getattr(block, "type", None) == "tool_use" and block.name == TOOL_NAME:
            tool_input = block.input
            if isinstance(tool_input, dict):
                return tool_input
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="Planner did not return a structured tool_use response",
    )


def _build_user_message(prompt: str, elements: list[ElementInput]) -> str:
    element_lines = "\n".join(
        f"- {e.id} (label: {e.label!r}, bbox: {e.bbox}, z_order: {e.z_order})"
        for e in elements
    )
    return (
        f"Elements available for animation:\n{element_lines}\n\n"
        f"User prompt: {prompt}"
    )


@router.post("/plan-animation", response_model=AnimationPlan)
def plan_animation(request: PlanAnimationRequest) -> AnimationPlan:
    messages: list[MessageParam] = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": PLANNER_PROMPT},
                {
                    "type": "text",
                    "text": _build_user_message(request.prompt, request.elements),
                },
            ],
        }
    ]

    client = get_anthropic_client()
    message = client.messages.create(
        model=PLANNER_MODEL,
        max_tokens=MAX_TOKENS,
        tools=[cast(ToolParam, PLANNER_TOOL)],
        tool_choice=cast(ToolChoiceToolParam, {"type": "tool", "name": TOOL_NAME}),
        messages=messages,
    )

    tool_input = _extract_tool_input(message)
    raw_plan = tool_input.get("plan", [])

    try:
        element_animations = [ElementAnimation.model_validate(item) for item in raw_plan]
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Planner returned invalid DSL: {exc.errors()}",
        ) from exc

    valid_ids = {e.id for e in request.elements}
    unknown = [ea.element_id for ea in element_animations if ea.element_id not in valid_ids]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Planner referenced unknown element_ids: {unknown}",
        )

    return AnimationPlan(image_id=request.image_id, plan=element_animations)
