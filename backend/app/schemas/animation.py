"""Animation DSL schema — GSAP-compatible per-element plan.

The DSL supports five primitives (``translate``, ``rotate``, ``scale``,
``opacity``, ``path-follow``). Each ``ElementAnimation`` groups a timeline of
primitives with default ``easing`` / ``loop`` / ``duration_ms`` that GSAP will
apply at the timeline level; primitives may override per-step if needed.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PrimitiveType = Literal["translate", "rotate", "scale", "opacity", "path-follow"]

PRIMITIVE_TYPES: tuple[PrimitiveType, ...] = (
    "translate",
    "rotate",
    "scale",
    "opacity",
    "path-follow",
)


class AnimationPrimitive(BaseModel):
    """A single step on an element's timeline.

    Only the fields relevant to ``type`` need to be populated; unused fields stay
    ``None`` so the DSL stays JSON-friendly for GSAP consumers.
    """

    model_config = ConfigDict(extra="forbid")

    type: PrimitiveType
    dx: float | None = None
    dy: float | None = None
    angle: float | None = None
    scale: float | None = None
    opacity: float | None = None
    path: list[list[float]] | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    easing: str | None = None


class ElementAnimation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    element_id: str = Field(..., min_length=1)
    timeline: list[AnimationPrimitive]
    easing: str = "power1.inOut"
    loop: bool = False
    duration_ms: int = Field(default=1000, ge=0)


class AnimationPlan(BaseModel):
    image_id: str
    plan: list[ElementAnimation]
