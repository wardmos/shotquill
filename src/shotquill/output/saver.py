# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Save a captured image to disk with macOS-style timestamped filenames."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from PIL import Image

from shotquill.capture.base import CaptureResult

_JPEG_FORMATS = {"jpg", "jpeg"}


def save(result: CaptureResult, directory: str, image_format: str = "png") -> Path:
    """Write ``result`` into ``directory`` and return the created file path."""
    out_dir = Path(directory).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    ext = "jpg" if image_format.lower() in _JPEG_FORMATS else "png"
    stamp = dt.datetime.now().strftime("%Y-%m-%d at %H.%M.%S")
    path = out_dir / f"Shotquill {stamp}.{ext}"

    image = Image.frombytes("RGBA", (result.width, result.height), result.pixels)
    if ext == "jpg":
        image = image.convert("RGB")
    image.save(path)
    return path
