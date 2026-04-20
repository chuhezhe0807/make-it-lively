"""POST /api/perception — identify semantic elements via Claude VLM."""
from __future__ import annotations

import base64
import json
from typing import Any, Final, cast

import anthropic
from anthropic.types import MessageParam, ToolChoiceToolParam, ToolParam
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app import config, storage

VLM_MODEL: Final[str] = "claude-opus-4-7"
MAX_TOKENS: Final[int] = 16000
TOOL_NAME: Final[str] = "report_elements"

IMAGE_MEDIA_TYPES: Final[dict[str, str]] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}

PERCEPTION_PROMPT: Final[str] = (
    # Core task: identify animatable foreground objects. Background is excluded
    # because it is reconstructed separately by /api/inpaint.
    "Analyze this image and identify the distinct foreground elements that "
    "could be animated independently (e.g. characters, vehicles, objects). "
    "Exclude the background. For each element, return a stable snake_case id, "
    "a short human-readable label, an axis-aligned bounding box in pixel "
    "coordinates as [x, y, width, height] with origin at the top-left, and a "
    "z_order where higher values render on top.\n\n"
    # Sub-part decomposition: only do this when the object has parts that
    # would plausibly move independently — a robot's arms, a bird's wings,
    # a character's head. Skip for rigid objects (balls, text, panels).
    "If an element is articulated — meaning it has distinct parts that could "
    "move independently (limbs, wings, wheels, doors, flames) — ALSO emit "
    "one element per sub-part. Use dotted ids like 'blue_robot.right_arm' "
    "and set `parent_id` to the parent's id. The set of children should "
    "cover the parent's bbox as tightly as possible so that hiding the "
    "parent during animation does not leave visible gaps.\n\n"
    # Pivot: Claude is great at spatial reasoning, so we ask it to locate
    # the natural rotation/scale anchor for each articulated sub-part.
    "For each articulated sub-part (and for standalone articulated elements "
    "like a pendulum), also return `pivot` as [x, y] pixel coordinates "
    "pointing at the natural rotation anchor — shoulder for an arm, hip "
    "for a leg, hinge for a door, base-center for a rocket flame. Omit "
    "`pivot` when there is no clear anchor.\n\n"
    "Call the report_elements tool."
)

PERCEPTION_TOOL: Final[dict[str, Any]] = {
    "name": TOOL_NAME,
    "description": "Report the foreground elements detected in the image.",
    "input_schema": {
        "type": "object",
        "properties": {
            "elements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "label": {"type": "string"},
                        "bbox": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 4,
                            "maxItems": 4,
                        },
                        "z_order": {"type": "integer"},
                        # Optional — only populated for articulated sub-parts.
                        "parent_id": {"type": ["string", "null"]},
                        # Optional [x, y] pixel coords for the rotation anchor.
                        "pivot": {
                            "type": ["array", "null"],
                            "items": {"type": "number"},
                            "minItems": 2,
                            "maxItems": 2,
                        },
                    },
                    "required": ["id", "label", "bbox", "z_order"],
                },
            },
        },
        "required": ["elements"],
    },
}

router = APIRouter(prefix="/api", tags=["perception"])


class PerceptionRequest(BaseModel):
    image_id: str = Field(..., min_length=1)


class Element(BaseModel):
    id: str
    label: str
    bbox: list[float] = Field(..., min_length=4, max_length=4)
    z_order: int
    # Optional parent id: set on articulated sub-parts so the frontend can
    # group children under their parent and hide the parent during animation
    # to avoid moving-arm-plus-static-arm ghosting.
    parent_id: str | None = None
    # Optional [x, y] pixel coords in image space — the natural rotation /
    # scale anchor for this element. The frontend converts to CSS
    # transform-origin as a percentage of the layer's intrinsic dimensions.
    pivot: list[float] | None = Field(default=None, min_length=2, max_length=2)


class PerceptionResponse(BaseModel):
    image_id: str
    elements: list[Element]


def get_anthropic_client() -> anthropic.Anthropic:
    """Return an Anthropic client; monkeypatched in tests.

    Honors ``ANTHROPIC_BASE_URL`` so traffic can be routed through a proxy
    or gateway. When the env var is unset, the SDK uses its default endpoint.
    """
    base_url = config.anthropic_base_url()
    if base_url is not None:
        return anthropic.Anthropic(base_url=base_url)
    return anthropic.Anthropic()


def _find_image(image_id: str) -> tuple[bytes, str]:
    for ext, media_type in IMAGE_MEDIA_TYPES.items():
        path = storage.IMAGES_DIR / f"{image_id}.{ext}"
        if path.exists():
            return path.read_bytes(), media_type
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Image not found: {image_id}",
    )


def _load_cache(image_id: str) -> PerceptionResponse | None:
    cache_path = storage.PERCEPTION_DIR / f"{image_id}.json"
    if not cache_path.exists():
        return None
    data = json.loads(cache_path.read_text())
    return PerceptionResponse.model_validate(data)


def _save_cache(response: PerceptionResponse) -> None:
    storage.PERCEPTION_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = storage.PERCEPTION_DIR / f"{response.image_id}.json"
    cache_path.write_text(response.model_dump_json())


def _extract_tool_input(message: Any) -> dict[str, Any]:
    for block in message.content:
        if getattr(block, "type", None) == "tool_use" and block.name == TOOL_NAME:
            tool_input = block.input
            if isinstance(tool_input, dict):
                return tool_input
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="VLM did not return a structured tool_use response",
    )


@router.post("/perception", response_model=PerceptionResponse)
def perceive_elements(request: PerceptionRequest) -> PerceptionResponse:
    cached = _load_cache(request.image_id)
    if cached is not None:
        return cached

    image_bytes, media_type = _find_image(request.image_id)
    image_b64 = base64.standard_b64encode(image_bytes).decode("ascii")

    messages: list[MessageParam] = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": cast(Any, media_type),
                        "data": image_b64,
                    },
                },
                {"type": "text", "text": PERCEPTION_PROMPT},
            ],
        }
    ]

    client = get_anthropic_client()
    message = client.messages.create(
        model=VLM_MODEL,
        max_tokens=MAX_TOKENS,
        tools=[cast(ToolParam, PERCEPTION_TOOL)],
        tool_choice=cast(ToolChoiceToolParam, {"type": "tool", "name": TOOL_NAME}),
        messages=messages,
    )

    tool_input = _extract_tool_input(message)
    elements = [Element.model_validate(e) for e in tool_input.get("elements", [])]
    response = PerceptionResponse(image_id=request.image_id, elements=elements)
    _save_cache(response)
    return response
