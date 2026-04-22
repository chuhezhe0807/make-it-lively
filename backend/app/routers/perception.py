"""POST /api/perception — identify semantic elements via Claude VLM."""
from __future__ import annotations

import base64
import json
import logging
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
    # Sub-part decomposition: any element with parts that could plausibly
    # move independently MUST be decomposed. This is critical for animation
    # quality — without sub-parts, limbs cannot move.
    "If an element is articulated — meaning it has distinct parts that could "
    "move independently — ALSO emit one element per sub-part. Use dotted "
    "ids like 'blue_robot.right_arm' and set `parent_id` to the parent's "
    "id. The set of children should cover the parent's bbox as tightly as "
    "possible so that hiding the parent during animation does not leave "
    "visible gaps.\n\n"
    # Mandatory decomposition for creatures and characters — this is the
    # single biggest lever for animation quality.
    "IMPORTANT — any animal, creature, person, or character with visible "
    "limbs MUST be decomposed into sub-parts, even if the pose is compact "
    "or limbs partially overlap. Typical decompositions:\n"
    "  - Cat/dog → head, body, front_legs, hind_legs, tail\n"
    "  - Bird → head, body, left_wing, right_wing, tail\n"
    "  - Person → head, torso, left_arm, right_arm, left_leg, right_leg\n"
    "  - Robot → head, torso, left_arm, right_arm, left_leg, right_leg\n"
    "  - Insect → head, thorax, left_wing, right_wing, legs\n"
    "When a limb is partially hidden (e.g. a side-view cat shows only two "
    "legs), decompose the VISIBLE parts — estimate the bbox even if the "
    "boundary is approximate. Rigid objects without movable parts (balls, "
    "text labels, static panels) should NOT be decomposed.\n\n"
    # Pivot: locate the natural rotation/scale anchor for each sub-part.
    "For each articulated sub-part, also return `pivot` as [x, y] pixel "
    "coordinates pointing at the natural rotation anchor — the JOINT where "
    "this part connects to its parent:\n"
    "  - front_legs → shoulder joint (top of the leg where it meets the body)\n"
    "  - hind_legs → hip joint (top of the rear leg)\n"
    "  - tail → tail base (where the tail meets the body)\n"
    "  - arm → shoulder\n"
    "  - head → neck base\n"
    "  - wing → wing root (where the wing attaches to the body)\n"
    "  - door → hinge\n"
    "  - pendulum → pivot point at the top\n"
    "Omit `pivot` only when there is genuinely no clear anchor.\n\n"
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


logger = logging.getLogger(__name__)

_COVERAGE_THRESHOLD = 0.70


def _validate_sub_part_coverage(elements: list[Element]) -> None:
    """Log a warning when child bboxes poorly cover their parent's bbox.

    This is a non-blocking diagnostic — it never raises.  The goal is to
    surface bad VLM decompositions in the logs so they can be investigated.
    """
    # Build parent → children mapping.
    children_by_parent: dict[str, list[Element]] = {}
    element_by_id: dict[str, Element] = {}
    for e in elements:
        element_by_id[e.id] = e
        if e.parent_id is not None:
            children_by_parent.setdefault(e.parent_id, []).append(e)

    for parent_id, children in children_by_parent.items():
        parent = element_by_id.get(parent_id)
        if parent is None:
            continue
        px, py, pw, ph = parent.bbox
        parent_area = pw * ph
        if parent_area <= 0:
            continue

        # Union area via pixel-grid painting on a boolean grid would be
        # accurate but expensive.  For axis-aligned bboxes a simple
        # sum-of-areas approximation (clamped to parent area) is enough
        # to catch gross misses.  Overlapping children inflate the sum
        # but that only causes false-negatives (no spurious warnings).
        child_area_sum = 0.0
        for child in children:
            cx, cy, cw, ch = child.bbox
            # Clamp child bbox to parent bounds for intersection.
            ix1 = max(px, cx)
            iy1 = max(py, cy)
            ix2 = min(px + pw, cx + cw)
            iy2 = min(py + ph, cy + ch)
            if ix2 > ix1 and iy2 > iy1:
                child_area_sum += (ix2 - ix1) * (iy2 - iy1)

        ratio = child_area_sum / parent_area
        if ratio < _COVERAGE_THRESHOLD:
            logger.warning(
                "Sub-part coverage for '%s' is %.0f%%, below %.0f%% threshold",
                parent_id,
                ratio * 100,
                _COVERAGE_THRESHOLD * 100,
            )


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
    _validate_sub_part_coverage(elements)
    response = PerceptionResponse(image_id=request.image_id, elements=elements)
    _save_cache(response)
    return response
