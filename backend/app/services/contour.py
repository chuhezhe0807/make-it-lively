"""Contour extraction, centroid computation, and mask feathering.

All public functions accept / return PIL ``Image`` objects and plain Python
lists so callers need no direct cv2 or numpy dependency.
"""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageFilter

from app import config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pil_to_binary(mask: Image.Image) -> np.ndarray:
    """Convert an L-mode PIL image to a strict binary uint8 array (0 / 255)."""
    if mask.mode != "L":
        mask = mask.convert("L")
    arr = np.array(mask, dtype=np.uint8)
    _, binary = cv2.threshold(arr, 127, 255, cv2.THRESH_BINARY)
    return binary


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_contour(mask: Image.Image) -> list[list[float]]:
    """Return the simplified polygon of the largest foreground region.

    Parameters
    ----------
    mask:
        PIL Image in ``L`` mode.  Non-zero pixels are foreground.

    Returns
    -------
    List of ``[x, y]`` vertex pairs.  Empty when no foreground exists.
    """
    binary = _pil_to_binary(mask)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return []

    # Keep only the largest contour to filter noise / fragments.
    largest = max(contours, key=cv2.contourArea)

    # Scale-adaptive simplification: epsilon is relative to perimeter.
    eps_factor = config.contour_epsilon()
    arc_length = cv2.arcLength(largest, closed=True)
    approx = cv2.approxPolyDP(largest, eps_factor * arc_length / 1000.0, closed=True)

    # ``approx`` shape is (N, 1, 2) → squeeze to (N, 2)
    points = approx.squeeze(axis=1)
    return [[float(p[0]), float(p[1])] for p in points]


def compute_centroid(contour: list[list[float]]) -> list[float]:
    """Geometric centroid of a contour polygon via image moments.

    Falls back to the vertex mean for degenerate (< 3 points) input.
    """
    if len(contour) < 3:
        if not contour:
            return [0.0, 0.0]
        xs = [p[0] for p in contour]
        ys = [p[1] for p in contour]
        return [sum(xs) / len(xs), sum(ys) / len(ys)]

    pts = np.array(contour, dtype=np.float32).reshape(-1, 1, 2).astype(np.int32)
    moments = cv2.moments(pts)
    if moments["m00"] == 0:
        xs = [p[0] for p in contour]
        ys = [p[1] for p in contour]
        return [sum(xs) / len(xs), sum(ys) / len(ys)]

    return [moments["m10"] / moments["m00"], moments["m01"] / moments["m00"]]


def feather_mask(mask: Image.Image, radius: int | None = None) -> Image.Image:
    """Gaussian-blur an L-mode mask to soften hard edges.

    At small radii (1–3 px) only the boundary transitions are affected;
    interior pixels surrounded by other 255-neighbours stay at 255 because
    blurring a plateau of equal values is a no-op.
    """
    if radius is None:
        radius = config.feather_radius()
    if radius <= 0:
        return mask
    return mask.filter(ImageFilter.GaussianBlur(radius=radius))
