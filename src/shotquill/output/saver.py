# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Save captured / annotated images to disk with macOS-style timestamped names."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

from shotquill.capture.base import CaptureResult

if TYPE_CHECKING:
    from PySide6.QtGui import QImage

_JPEG_FORMATS = {"jpg", "jpeg"}


def build_output_path(directory: str, image_format: str = "png") -> Path:
    """Create ``directory`` if needed and return a fresh timestamped file path."""
    out_dir = Path(directory).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = "jpg" if image_format.lower() in _JPEG_FORMATS else "png"
    stamp = dt.datetime.now().strftime("%Y-%m-%d at %H.%M.%S")
    return out_dir / f"ShotQuill {stamp}.{ext}"


def save(result: CaptureResult, directory: str, image_format: str = "png") -> Path:
    """Write a raw capture to disk and return the created file path."""
    path = build_output_path(directory, image_format)
    image = Image.frombytes("RGBA", (result.width, result.height), result.pixels)
    if path.suffix == ".jpg":
        image = image.convert("RGB")
    image.save(path)
    return path


def save_qimage(image: QImage, directory: str, image_format: str = "png") -> Path:
    """Write an annotated QImage (rendered by the editor) to disk."""
    path = build_output_path(directory, image_format)
    image.save(str(path))
    return path
