# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Stitch a run of scrolling frames into one long image.

A long screenshot is many region grabs taken while the content scrolls, with each
frame overlapping the one before. This module is the platform-independent core: it
estimates how far the content moved between consecutive frames and composites the
new strip onto a growing canvas — no capture, no input synthesis, no Qt event
loop, so it is fully unit-testable headlessly.

Two wrinkles a naïve "paste each frame below the last" gets wrong, both handled
here:

* **Overlap.** Frames share a band of rows (the scroll step is smaller than the
  frame, on purpose, so there is something to align on). Pasting whole frames
  would duplicate that band; we measure the per-pair vertical offset and append
  only the genuinely new rows.
* **Sticky header / footer.** A fixed top bar or bottom bar repeats identically in
  every frame at the same position. Left in, it would march down the middle of the
  long image. We detect the unchanging top/bottom bands and keep each exactly
  once — header at the very top, footer at the very bottom.

The matcher assumes the overlap is pixel-identical, which holds when frames are
grabbed after each scroll settles (static content, native resolution). Mid-scroll
motion blur or sub-pixel reflow is out of scope for now.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtGui import QImage

# How many leading rows of the newer frame must line up with the older one for an
# overlap candidate to be considered. Small and cheap as a first filter; the real
# offset is then confirmed against the *whole* overlap, so a coincidental k-row
# match inside a blank or repeating band can't fix the offset on its own.
DEFAULT_MIN_OVERLAP = 8


def _rows(image: QImage) -> tuple[list[bytes], int, int]:
    """Split an image into a list of per-row RGBA byte strings, plus (w, h).

    Rows are compared by exact bytes (not a hash) so equality is collision-free.
    Uses the image's own stride and trims each row to ``width * 4`` so any
    end-of-line padding never leaks into the comparison.
    """
    from PySide6.QtGui import QImage

    img = image.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    stride = img.bytesPerLine()
    row_bytes = w * 4
    buf = bytes(img.constBits())[: stride * h]
    return [buf[y * stride : y * stride + row_bytes] for y in range(h)], w, h


def _sticky_from_rows(prev: list[bytes], curr: list[bytes], h: int) -> tuple[int, int]:
    """Leading/trailing run of rows identical at the same index between two frames."""
    head = 0
    while head < h and prev[head] == curr[head]:
        head += 1
    foot = 0
    while foot < h - head and prev[h - 1 - foot] == curr[h - 1 - foot]:
        foot += 1
    return head, foot


def _offset_from_rows(
    prev: list[bytes], curr: list[bytes], h: int, head: int, foot: int, min_overlap: int
) -> int | None:
    """Vertical scroll offset between two frames within the scrolling band.

    Returns the smallest ``dy`` such that ``curr``'s band, shifted up by ``dy``,
    matches ``prev``'s band over their entire overlap (a ``min_overlap``-row anchor
    is the cheap first filter; the full overlap then confirms it) — i.e. the content
    scrolled up by ``dy``. ``0`` means the band is unchanged (no scroll / reached
    the bottom); ``None`` means no overlap was found (the frames are disjoint,
    e.g. a scroll larger than the visible band).
    """
    band = h - head - foot
    if band <= 0:
        return 0
    k = min(min_overlap, band)
    anchor = curr[head : head + k]
    for dy in range(0, band - k + 1):
        if prev[head + dy : head + dy + k] != anchor:
            continue
        # The k-row anchor matched; confirm the *whole* remaining overlap agrees
        # before trusting it. A blank or repeating band can match the anchor at a
        # dy smaller than the real scroll, and appending only those rows would drop
        # the genuinely-new content below — so the true offset is the smallest dy
        # whose entire overlap lines up, not merely its first k rows.
        if prev[head + dy : h - foot] == curr[head : h - foot - dy]:
            return dy
    return None


def detect_sticky_bands(prev: QImage, curr: QImage) -> tuple[int, int]:
    """Height of the unchanging top and bottom bands shared by two frames.

    Returns ``(header, footer)`` in pixels. A sticky header/footer is rows that
    are byte-identical at the same position in both frames; everything between is
    the part that scrolls. Returns ``(0, 0)`` when the frames differ in size.
    """
    pr, w1, h1 = _rows(prev)
    cr, w2, h2 = _rows(curr)
    if (w1, h1) != (w2, h2):
        return (0, 0)
    return _sticky_from_rows(pr, cr, h1)


def estimate_vertical_offset(
    prev: QImage,
    curr: QImage,
    *,
    head: int = 0,
    foot: int = 0,
    min_overlap: int = DEFAULT_MIN_OVERLAP,
) -> int | None:
    """How far the content scrolled between two consecutive frames, in pixels.

    ``head`` / ``foot`` exclude a known sticky header / footer from the match (see
    :func:`detect_sticky_bands`); leave them ``0`` for content with no fixed bars.
    Returns ``0`` for an unchanged band (no scroll), a positive offset for a
    scroll, or ``None`` when no overlap can be found. Frames of differing size
    yield ``None``.
    """
    pr, w1, h1 = _rows(prev)
    cr, w2, h2 = _rows(curr)
    if (w1, h1) != (w2, h2):
        return None
    return _offset_from_rows(pr, cr, h1, head, foot, min_overlap)


def stitch_vertical(frames: list[QImage], *, min_overlap: int = DEFAULT_MIN_OVERLAP) -> QImage:
    """Composite overlapping scrolling frames into one tall image.

    Frames must share a width (raises ``ValueError`` otherwise) and are assumed to
    be in scroll order. A sticky header/footer common to the run is kept once; the
    overlap between each pair is measured and only the new rows are appended. A pair
    with no detectable overlap is treated as disjoint and its whole scrolling band
    is appended (content is preserved rather than dropped).

    Returns a copy of the sole frame when given one, and raises ``ValueError`` on
    an empty input.
    """
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage, QPainter

    imgs = [f for f in frames if f is not None and not f.isNull()]
    if not imgs:
        raise ValueError("stitch_vertical needs at least one frame")
    if len(imgs) == 1:
        return imgs[0].copy()

    rows_list: list[list[bytes]] = []
    width = h = None
    for f in imgs:
        rows, w, fh = _rows(f)
        if width is None:
            width, h = w, fh
        elif (w, fh) != (width, h):
            raise ValueError(f"frames differ in size: {w}x{fh} != {width}x{h}")
        rows_list.append(rows)

    # Pass 1: the sticky header/footer is what stays identical across *every* pair
    # that actually scrolled, so take the minimum run over those pairs — a
    # coincidental extra match in one pair can only inflate its own run, never the
    # minimum.
    heads: list[int] = []
    foots: list[int] = []
    for i in range(1, len(imgs)):
        head_i, foot_i = _sticky_from_rows(rows_list[i - 1], rows_list[i], h)
        if head_i < h:  # the pair differs somewhere → it scrolled / changed
            heads.append(head_i)
            foots.append(foot_i)
    if not heads:  # every frame identical to its neighbour — nothing scrolled
        return imgs[0].copy()
    head = min(heads)
    foot = min(foots)
    band = h - head - foot

    # Pass 2: per-pair offsets under the global sticky bands. A disjoint pair
    # (None) contributes its whole band as new content.
    offsets: list[int] = []
    for i in range(1, len(imgs)):
        dy = _offset_from_rows(rows_list[i - 1], rows_list[i], h, head, foot, min_overlap)
        offsets.append(band if dy is None else dy)

    total_h = h + sum(offsets)
    canvas = QImage(width, total_h, QImage.Format.Format_RGBA8888)
    canvas.fill(0)

    painter = QPainter(canvas)
    try:
        first = imgs[0]
        if head > 0:
            painter.drawImage(QRect(0, 0, width, head), first, QRect(0, 0, width, head))
        painter.drawImage(QRect(0, head, width, band), first, QRect(0, head, width, band))
        y = head + band
        for i in range(1, len(imgs)):
            dy = offsets[i - 1]
            if dy <= 0:
                continue
            src_y = h - foot - dy
            painter.drawImage(QRect(0, y, width, dy), imgs[i], QRect(0, src_y, width, dy))
            y += dy
        if foot > 0:
            painter.drawImage(QRect(0, y, width, foot), imgs[-1], QRect(0, h - foot, width, foot))
    finally:
        painter.end()
    return canvas


class ScrollAccumulator:
    """Stitch a live scrolling capture incrementally, frame by frame.

    The sample → measure → append → stop logic, shared by both drivers: the
    blocking CLI loop in :func:`shotquill.headless.perform_scrolling_capture` and
    the GUI's non-blocking ``QTimer`` (each tick feeds one frame). Feed frames with
    :meth:`add`; when it returns ``False`` the scroll has settled or hit a limit,
    and :meth:`result` returns the finished long image.

    Unlike a batch :func:`stitch_vertical`, this keeps only what it needs as it
    goes — the header, the appended new-row strips, the most recent frame (for the
    footer), and the previous frame's rows (for the next comparison) — so memory is
    bounded by the *output* size plus one frame, not by the frame count. A sticky
    header / footer is detected from the first scrolling pair and excluded from the
    offset match (so a fixed top bar can't masquerade as "no scroll") and kept once
    at the top / bottom. A frame that did not move (offset ``0``) is counted toward
    ``settle`` and dropped. The capture stops after ``settle`` still frames, once
    the height would exceed ``max_height``, or at the ``max_frames`` safety cap.
    """

    def __init__(
        self,
        *,
        max_height: int,
        settle: int,
        max_frames: int,
        min_overlap: int = DEFAULT_MIN_OVERLAP,
    ) -> None:
        self._max_height = max_height
        self._settle = settle
        self._max_frames = max_frames
        self._min_overlap = min_overlap
        self._first: QImage | None = None  # held until the first scroll seeds the body
        self._prev_rows: list[bytes] | None = None  # rows of the most recent kept frame
        self._last: QImage | None = None  # most recent kept frame — source of the footer
        self._header: QImage | None = None  # the sticky header, kept once
        self._body: list[QImage] = []  # new-row strips, in scroll order (already cropped)
        self._head = 0
        self._foot = 0
        self._sticky_set = False
        self._body_height = 0
        self._width = 0
        self._frame_h = 0
        self._count = 0
        self._unchanged = 0
        self._done = False

    @property
    def frame_count(self) -> int:
        return self._count

    @property
    def done(self) -> bool:
        return self._done

    def add(self, image: QImage) -> bool:
        """Take one sampled frame; return ``True`` to keep sampling, ``False`` when finished."""
        if self._done:
            return False
        rows, w, h = _rows(image)
        if self._prev_rows is None:
            self._first = image
            self._last = image
            self._prev_rows = rows
            self._width, self._frame_h = w, h
            self._count = 1
            return True
        if (w, h) != (self._width, self._frame_h):
            # The region's pixel size changed under us (display reconfigured); the
            # frames can no longer be stitched, so finish with what we have.
            self._done = True
            return False
        if not self._sticky_set:
            self._seed_body(rows)
        dy = _offset_from_rows(self._prev_rows, rows, h, self._head, self._foot, self._min_overlap)
        if dy == 0:
            # The view did not move since the last sample. Count it toward the
            # settle threshold and drop the duplicate (don't advance ``prev``).
            self._unchanged += 1
            if self._unchanged >= self._settle:
                self._done = True
                return False
            return True
        self._unchanged = 0
        band = h - self._head - self._foot
        # dy rows of new content sit at the bottom of the band; a disjoint sample
        # (None) shares no overlap, so its whole band is new.
        new_rows = band if dy is None else dy
        src_y = self._head if dy is None else (h - self._foot - dy)
        self._body.append(image.copy(0, src_y, w, new_rows))
        self._body_height += new_rows
        self._last = image
        self._prev_rows = rows
        self._count += 1
        if (
            self._head + self._body_height + self._foot >= self._max_height
            or self._count >= self._max_frames
        ):
            self._done = True
            return False
        return True

    def _seed_body(self, curr_rows: list[bytes]) -> None:
        """On the first scrolling pair, lock the sticky bands and seed the body with
        the first frame's band (header/footer are composited once at the end)."""
        self._head, self._foot = _sticky_from_rows(self._prev_rows, curr_rows, self._frame_h)
        first = self._first
        band = self._frame_h - self._head - self._foot
        if self._head > 0:
            self._header = first.copy(0, 0, self._width, self._head)
        self._body.append(first.copy(0, self._head, self._width, band))
        self._body_height = band
        self._sticky_set = True
        self._first = None  # the band/header are captured; drop the full first frame

    def result(self) -> QImage:
        """Composite the finished long image, cropped to ``max_height``."""
        if not self._sticky_set:
            # Never scrolled past the first frame — it is the whole result.
            if self._first is None:
                raise ValueError("ScrollAccumulator has no frames")
            return self._first.copy()

        from PySide6.QtCore import QRect
        from PySide6.QtGui import QImage, QPainter

        total = self._head + self._body_height + self._foot
        canvas = QImage(self._width, total, QImage.Format.Format_RGBA8888)
        canvas.fill(0)
        painter = QPainter(canvas)
        try:
            y = 0
            if self._header is not None:
                painter.drawImage(QRect(0, 0, self._width, self._head), self._header)
                y = self._head
            for strip in self._body:
                painter.drawImage(QRect(0, y, self._width, strip.height()), strip)
                y += strip.height()
            if self._foot > 0 and self._last is not None:
                painter.drawImage(
                    QRect(0, y, self._width, self._foot),
                    self._last,
                    QRect(0, self._frame_h - self._foot, self._width, self._foot),
                )
        finally:
            painter.end()
        if canvas.height() > self._max_height:
            canvas = canvas.copy(0, 0, self._width, self._max_height)
        return canvas
