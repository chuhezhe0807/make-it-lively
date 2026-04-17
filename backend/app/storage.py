"""Filesystem storage layout for uploaded assets and derived artifacts."""
from __future__ import annotations

from pathlib import Path

BACKEND_ROOT: Path = Path(__file__).resolve().parents[1]
STORAGE_ROOT: Path = BACKEND_ROOT / "storage"
IMAGES_DIR: Path = STORAGE_ROOT / "images"
PERCEPTION_DIR: Path = STORAGE_ROOT / "perception"
LAYERS_DIR: Path = STORAGE_ROOT / "layers"
