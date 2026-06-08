# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Paint solid blocks over blocklisted windows inside a captured frame.

When a full-screen or region capture contains a window the user blocklisted,
that window's pixels are overwritten with an opaque fill — a real edit of the
bytes, not a display-layer overlay, so the sensitive pixels never exist in the
saved or returned image. A solid fill (rather than mosaic or blur) is the
clearest "hidden here" signal and leaves zero recoverable residue.

The maths is pure and lives here so it is unit-testable without a real screen:
window bounds are logical (point) coordinates; the captured image is physical
pixels at ``scale``, with its top-left at ``origin`` in the same logical space.
Edges are rounded outward (floor the top-left, ceil the bottom-right) so the
block always fully covers the window rather than leaving a one-pixel seam.
"""

from __future__ import annotations

import math

from shotquill.capture.base import CaptureResult, Rect

# Opaque black RGBA. Valid for both straight and premultiplied alpha (the
# colour channels are zero either way), so it matches whatever the backend
# produced without a format conversion.
_FILL = bytes((0, 0, 0, 255))


def pixel_rect(
    image_w: int, image_h: int, scale: float, origin: tuple[int, int], bounds: Rect
) -> tuple[int, int, int, int] | None:
    """Map a window's logical ``bounds`` to a pixel rectangle in the image,
    clipped to its edges. Returns ``None`` when nothing lands inside."""
    ox, oy = origin
    x0 = math.floor((bounds.x - ox) * scale)
    y0 = math.floor((bounds.y - oy) * scale)
    x1 = math.ceil((bounds.x + bounds.width - ox) * scale)
    y1 = math.ceil((bounds.y + bounds.height - oy) * scale)
    x0, x1 = max(0, min(image_w, x0)), max(0, min(image_w, x1))
    y0, y1 = max(0, min(image_h, y0)), max(0, min(image_h, y1))
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def fill_rects(result: CaptureResult, rects: list[tuple[int, int, int, int]]) -> CaptureResult:
    """Return a copy of ``result`` with each pixel rectangle filled opaque."""
    buf = bytearray(result.pixels)
    stride = result.width * 4
    for x0, y0, x1, y1 in rects:
        row = _FILL * (x1 - x0)
        for y in range(y0, y1):
            start = y * stride + x0 * 4
            buf[start : start + (x1 - x0) * 4] = row
    return CaptureResult(
        width=result.width,
        height=result.height,
        scale=result.scale,
        pixels=bytes(buf),
        premultiplied=result.premultiplied,
    )


def redact_bounds(
    result: CaptureResult, origin: tuple[int, int], bounds: list[Rect]
) -> tuple[CaptureResult, int]:
    """Fill every window in ``bounds`` that lands inside ``result``.

    Returns the (possibly unchanged) result and the number of windows that
    actually intersected the frame — a window fully off-screen redacts nothing.
    """
    rects = []
    for b in bounds:
        rect = pixel_rect(result.width, result.height, result.scale, origin, b)
        if rect is not None:
            rects.append(rect)
    if not rects:
        return result, 0
    return fill_rects(result, rects), len(rects)
