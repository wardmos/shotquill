# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Bridge raw capture pixels into Qt's QImage."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shotquill.capture.base import CaptureResult, Rect

if TYPE_CHECKING:
    from PySide6.QtGui import QImage

# Default mosaic cell size (source pixels per block) when blurring everything but
# the revealed regions. Coarse on purpose: the point is minimization, and a large
# cell leaves nothing legible outside the action window.
BLUR_BLOCK = 24


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


def pixelate_except(image: QImage, reveal: list[Rect], scale: float, block: int = BLUR_BLOCK):
    """Mosaic the whole frame, then paint the ``reveal`` rectangles back sharp.

    Privacy layer 4 (D15): blur everything by default and open a clear window only
    where the action is, so a recorded frame shows *what the agent did* without
    leaving the rest of the screen legible. ``reveal`` rectangles are logical
    points (the same space as ``--mask`` / window bounds), mapped to pixels via
    ``scale``. Returns ``image`` unchanged when nothing is revealed (there is then
    no reason to have blurred — the caller asked to keep some region clear).

    The mosaic is a smooth down-then-hard-up scale: each cell is the average of
    its source block (a single high-contrast pixel cannot survive), matching the
    editor's redaction mosaic. Not reversible to the original, but the revealed
    window is fully legible — minimization, not a guarantee.
    """
    if not reveal:
        return image
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QPainter

    w, h = image.width(), image.height()
    small = image.scaled(
        max(1, w // block), max(1, h // block), Qt.IgnoreAspectRatio, Qt.SmoothTransformation
    )
    mosaic = small.scaled(w, h, Qt.IgnoreAspectRatio, Qt.FastTransformation)

    painter = QPainter(mosaic)
    for r in reveal:
        x0, y0 = round(r.x * scale), round(r.y * scale)
        x1, y1 = round((r.x + r.width) * scale), round((r.y + r.height) * scale)
        x0, x1 = max(0, min(w, x0)), max(0, min(w, x1))
        y0, y1 = max(0, min(h, y0)), max(0, min(h, y1))
        if x1 <= x0 or y1 <= y0:
            continue
        rect = QRect(x0, y0, x1 - x0, y1 - y0)
        # Same-size source and dest rect: a 1:1 copy of the original sharp pixels.
        painter.drawImage(rect, image, rect)
    painter.end()
    return mosaic
