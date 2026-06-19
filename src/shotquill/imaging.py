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


def downscale_to_max(image: QImage, max_dimension: int) -> QImage:
    """Shrink ``image`` so its longer side is at most ``max_dimension`` pixels.

    A flight-recorder archive doesn't need native resolution to be reviewable, so
    capping the long edge trades a little detail for much smaller frames (cost
    control). Aspect ratio is preserved and the image is only ever scaled
    *down* — ``max_dimension <= 0`` or an already-small image is returned
    unchanged, so this is a no-op unless a cap is asked for and exceeded.
    """
    if max_dimension <= 0:
        return image
    longest = max(image.width(), image.height())
    if longest <= max_dimension:
        return image
    from PySide6.QtCore import Qt

    # KeepAspectRatio fits the image inside the square box, so the longer side
    # lands exactly on max_dimension and the shorter side scales with it.
    return image.scaled(max_dimension, max_dimension, Qt.KeepAspectRatio, Qt.SmoothTransformation)


# How small the before/after pair is scaled before diffing. The change box is a
# review hint, not a measurement, so a coarse grid keeps the per-pixel scan cheap
# (a 128px-longest-edge buffer is at most ~64 KB) while still locating the region.
DIFF_WORK_SIZE = 128


def changed_bbox(
    before: bytes, after: bytes, width: int, height: int, *, threshold: int = 16
) -> tuple[int, int, int, int] | None:
    """Pixel box ``(x0, y0, x1, y1)`` of where two RGBA buffers differ (pure).

    A pixel counts as changed when any RGBA channel differs by more than
    ``threshold`` — a margin that absorbs the minor resampling noise from scaling
    the frames down first. Returns ``None`` when either buffer is too small for the
    given size or nothing changed. Rows are compared whole first, so an unchanged
    row is skipped without touching its pixels (the common case is a small change).
    No Qt: callers scale to a small working buffer so this stays cheap.
    """
    span = width * height * 4
    if width <= 0 or height <= 0 or len(before) < span or len(after) < span:
        return None
    row_bytes = width * 4
    x0, y0, x1, y1 = width, height, -1, -1
    for y in range(height):
        base = y * row_bytes
        if before[base : base + row_bytes] == after[base : base + row_bytes]:
            continue  # identical row — skip the per-pixel scan
        for x in range(width):
            i = base + x * 4
            if (
                abs(before[i] - after[i]) > threshold
                or abs(before[i + 1] - after[i + 1]) > threshold
                or abs(before[i + 2] - after[i + 2]) > threshold
                or abs(before[i + 3] - after[i + 3]) > threshold
            ):
                x0, x1 = min(x0, x), max(x1, x)
                y0, y1 = min(y0, y), max(y1, y)
    if x1 < 0:
        return None
    return (x0, y0, x1 + 1, y1 + 1)


def frame_diff_fraction(
    before: QImage, after: QImage, *, work_size: int = DIFF_WORK_SIZE, threshold: int = 16
) -> tuple[float, float, float, float] | None:
    """Changed region of a before/after pair as fractions ``(x, y, w, h)`` of the frame.

    Scales both frames into a small ``work_size`` box (Qt does the work) and diffs
    them with :func:`changed_bbox`, then returns the box as fractions in ``[0, 1]``
    so the filmstrip can overlay it at any display size. Best-effort: returns
    ``None`` when the two frames scale to different sizes (e.g. different aspect)
    or nothing changed.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage

    def small(img: QImage) -> QImage:
        scaled = img.scaled(work_size, work_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return scaled.convertToFormat(QImage.Format.Format_RGBA8888)

    b, a = small(before), small(after)
    if (b.width(), b.height()) != (a.width(), a.height()) or b.width() == 0:
        return None
    w, h = b.width(), b.height()
    box = changed_bbox(
        bytes(b.constBits())[: w * h * 4],
        bytes(a.constBits())[: w * h * 4],
        w,
        h,
        threshold=threshold,
    )
    if box is None:
        return None
    x0, y0, x1, y1 = box
    return (x0 / w, y0 / h, (x1 - x0) / w, (y1 - y0) / h)


def pixelate_except(image: QImage, reveal: list[Rect], scale: float, block: int = BLUR_BLOCK):
    """Mosaic the whole frame, then paint the ``reveal`` rectangles back sharp.

    The reveal privacy layer: blur everything by default and open a clear window only
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
