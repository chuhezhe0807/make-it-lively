"""Tests for the contour extraction and centroid computation service."""
from __future__ import annotations

from PIL import Image, ImageDraw

from app.services.contour import compute_centroid, extract_contour, feather_mask

# ---------------------------------------------------------------------------
# extract_contour
# ---------------------------------------------------------------------------


def test_extract_contour_square_mask() -> None:
    """A solid white square on a black canvas produces ~4 vertices."""
    mask = Image.new("L", (60, 60), color=0)
    draw = ImageDraw.Draw(mask)
    draw.rectangle([15, 15, 45, 45], fill=255)

    contour = extract_contour(mask)
    assert len(contour) >= 4
    # All points should lie within or on the square boundary.
    xs = [p[0] for p in contour]
    ys = [p[1] for p in contour]
    assert min(xs) >= 14 and max(xs) <= 46
    assert min(ys) >= 14 and max(ys) <= 46


def test_extract_contour_empty_mask() -> None:
    """An all-black mask returns an empty contour."""
    mask = Image.new("L", (32, 32), color=0)
    assert extract_contour(mask) == []


def test_extract_contour_keeps_largest_region() -> None:
    """When the mask has two separate regions, only the largest is kept."""
    mask = Image.new("L", (100, 100), color=0)
    draw = ImageDraw.Draw(mask)
    # Large region (30x30 = 900 px)
    draw.rectangle([10, 10, 40, 40], fill=255)
    # Small region (5x5 = 25 px)
    draw.rectangle([80, 80, 85, 85], fill=255)

    contour = extract_contour(mask)
    # Contour should surround the large region, not the small one.
    xs = [p[0] for p in contour]
    assert all(x <= 50 for x in xs), "Contour should only cover the large region"


# ---------------------------------------------------------------------------
# compute_centroid
# ---------------------------------------------------------------------------


def test_compute_centroid_square() -> None:
    """Centroid of a square from (10,10) to (30,30) is near (20, 20)."""
    contour = [[10.0, 10.0], [30.0, 10.0], [30.0, 30.0], [10.0, 30.0]]
    cx, cy = compute_centroid(contour)
    assert abs(cx - 20.0) < 1.5
    assert abs(cy - 20.0) < 1.5


def test_compute_centroid_empty() -> None:
    assert compute_centroid([]) == [0.0, 0.0]


def test_compute_centroid_two_points_fallback() -> None:
    """With fewer than 3 points, falls back to vertex mean."""
    cx, cy = compute_centroid([[10.0, 20.0], [30.0, 40.0]])
    assert abs(cx - 20.0) < 0.1
    assert abs(cy - 30.0) < 0.1


# ---------------------------------------------------------------------------
# feather_mask
# ---------------------------------------------------------------------------


def test_feather_mask_zero_radius_is_identity() -> None:
    mask = Image.new("L", (20, 20), color=200)
    result = feather_mask(mask, radius=0)
    assert list(result.getdata()) == list(mask.getdata())


def test_feather_mask_interior_unchanged() -> None:
    """Interior pixels of a large solid region stay at 255 with a small radius."""
    mask = Image.new("L", (50, 50), color=0)
    draw = ImageDraw.Draw(mask)
    draw.rectangle([10, 10, 40, 40], fill=255)

    feathered = feather_mask(mask, radius=2)
    # Centre of the solid region should still be 255.
    assert feathered.getpixel((25, 25)) == 255
