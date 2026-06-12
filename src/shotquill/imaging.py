# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Bridge raw capture pixels into Qt's QImage."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shotquill.capture.base import CaptureResult

if TYPE_CHECKING:
    from PySide6.QtGui import QImage


def result_to_qimage(result: CaptureResult) -> QImage:
    from PySide6.QtGui import QImage

    # Window captures arrive with premultiplied alpha (so transparent rounded
    # corners render correctly); screen grabs are straight RGBA.
    fmt = (
        QImage.Format.Format_RGBA8888_Premultiplied
        if result.premultiplied
        else QImage.Format.Format_RGBA8888
    )
    # QImage trusts these dimensions and reads ``height * width * 4`` bytes from
    # the buffer with no bounds check — a short or mis-sized ``pixels`` would be
    # an out-of-bounds read (garbage or a crash). Fail loudly instead.
    if result.width <= 0 or result.height <= 0:
        raise ValueError(f"capture has non-positive size {result.width}x{result.height}")
    expected = result.width * result.height * 4
    if len(result.pixels) < expected:
        raise ValueError(
            f"capture buffer is {len(result.pixels)} bytes, need {expected} "
            f"for {result.width}x{result.height} RGBA"
        )
    image = QImage(result.pixels, result.width, result.height, fmt)
    # Detach from the Python bytes before they can be garbage collected.
    return image.copy()
