# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Pure geometry helpers for the overlays (no Qt dependency, unit-testable)."""

from __future__ import annotations

import math
from collections.abc import Sequence


def selection_rect(x0: float, y0: float, x1: float, y1: float) -> tuple[float, float, float, float]:
    """Normalize a drag from ``(x0, y0)`` to ``(x1, y1)`` into ``(x, y, w, h)``.

    Works regardless of drag direction (the rectangle is always positive-sized).
    """
    return (min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))


def scale_rect(
    rect: tuple[float, float, float, float], sx: float, sy: float
) -> tuple[int, int, int, int]:
    """Scale a logical rect to integer physical pixels (e.g. for Retina cropping)."""
    x, y, w, h = rect
    return (round(x * sx), round(y * sy), round(w * sx), round(h * sy))


def scale_rect_edges(
    rect: tuple[float, float, float, float], sx: float, sy: float
) -> tuple[int, int, int, int]:
    """Scale a logical rect to physical pixels by its *edges*.

    Floors the left/top edge and ceils the right/bottom edge so the physical
    rect covers every pixel the logical selection touches. Rounding ``x`` and
    ``width`` independently (as :func:`scale_rect` does, which is fine for
    painting) can clip up to a pixel at the right/bottom edge under fractional
    scale factors — use this variant when cropping.
    """
    x, y, w, h = rect
    left = math.floor(x * sx)
    top = math.floor(y * sy)
    right = math.ceil((x + w) * sx)
    bottom = math.ceil((y + h) * sy)
    return (left, top, right - left, bottom - top)


def crop_edge_hits(
    px: float, py: float, width: float, height: float, margin: float
) -> tuple[bool, bool, bool, bool]:
    """Which edges of a ``width``x``height`` box the pointer at ``(px, py)`` grabs.

    ``(px, py)`` is the pointer relative to the box's top-left. Returns
    ``(left, top, right, bottom)``: an edge counts as grabbed when the pointer is
    within ``margin`` of it (on either side — the grab band straddles the edge,
    so a handle is easy to catch) and the pointer is within ``margin`` of the box
    overall, so a pointer far away grabs nothing. A corner naturally lights two
    edges at once (the caller turns that into a diagonal resize).

    A box thinner/shorter than ``2*margin`` would light both opposite edges at
    once; only the nearer one is kept so a drag always moves a single edge.
    """
    if not (-margin <= px <= width + margin and -margin <= py <= height + margin):
        return (False, False, False, False)
    left = abs(px) <= margin
    right = abs(px - width) <= margin
    top = abs(py) <= margin
    bottom = abs(py - height) <= margin
    if left and right:
        left, right = abs(px) <= abs(px - width), abs(px) > abs(px - width)
    if top and bottom:
        top, bottom = abs(py) <= abs(py - height), abs(py) > abs(py - height)
    return (left, top, right, bottom)


def resize_selection(
    sel: tuple[float, float, float, float],
    edges: tuple[bool, bool, bool, bool],
    gx: float,
    gy: float,
    bounds: tuple[float, float, float, float],
    min_size: float,
) -> tuple[float, float, float, float]:
    """Move the active ``edges`` of ``sel`` to the pointer at ``(gx, gy)``.

    ``sel`` and ``bounds`` are ``(x, y, w, h)``; ``edges`` is
    ``(left, top, right, bottom)``. ``(gx, gy)`` is an absolute point in the same
    space as ``sel``/``bounds`` (e.g. logical global points), so the caller can
    feed the raw pointer without any window-relative remapping. Each moved edge is
    clamped so it stays inside ``bounds`` and never crosses the opposite edge
    closer than ``min_size`` — the same floor and outer limits the keyboard nudge
    applies, so dragging and arrow-keying agree. Returns a new ``(x, y, w, h)``.
    """
    left_e, top_e, right_e, bottom_e = edges
    x, y, w, h = sel
    bx, by, bw, bh = bounds
    left, top, right, bottom = x, y, x + w, y + h
    if left_e:
        left = min(max(gx, bx), right - min_size)
    if right_e:
        right = max(min(gx, bx + bw), left + min_size)
    if top_e:
        top = min(max(gy, by), bottom - min_size)
    if bottom_e:
        bottom = max(min(gy, by + bh), top + min_size)
    return (left, top, right - left, bottom - top)


def move_rect_within(
    sel: tuple[float, float, float, float], bounds: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    """Shift ``sel`` minimally so it lies inside ``bounds`` (sizes unchanged).

    Used when dragging the whole selection: the caller computes the desired
    top-left from the pointer, then this clamps the box back inside the
    virtual-desktop bounds without resizing it. Both are ``(x, y, w, h)``.
    """
    x, y, w, h = sel
    bx, by, bw, bh = bounds
    x = min(max(x, bx), bx + bw - w)
    y = min(max(y, by), by + bh - h)
    return (x, y, w, h)


def loupe_anchor(
    x: float, y: float, w: float, h: float, bound_w: float, bound_h: float, offset: float
) -> tuple[float, float]:
    """Top-left corner for a loupe of ``w``x``h`` following the pointer at ``(x, y)``.

    The loupe sits below-right of the pointer by ``offset`` and flips to the
    other side of each axis independently when it would leave the
    ``bound_w``x``bound_h`` area, so it never covers the pixels being inspected.
    """
    ax = x + offset
    if ax + w > bound_w:
        ax = x - offset - w
    ay = y + offset
    if ay + h > bound_h:
        ay = y - offset - h
    return (max(ax, 0.0), max(ay, 0.0))


def window_at_point(
    boxes: Sequence[tuple[float, float, float, float]], x: float, y: float
) -> int | None:
    """Index of the front-most box containing ``(x, y)``, or None.

    ``boxes`` are ``(x, y, w, h)`` rectangles ordered front-to-back, so the first
    match is the window the user is pointing at. The right/bottom edges are
    exclusive.
    """
    for index, (bx, by, bw, bh) in enumerate(boxes):
        if bx <= x < bx + bw and by <= y < by + bh:
            return index
    return None


def rect_containing(
    rects: Sequence[tuple[float, float, float, float]], x: float, y: float
) -> tuple[float, float, float, float] | None:
    """The first rect ``(x, y, w, h)`` containing ``(x, y)``, or None.

    Used to clip the full-span crosshair guide lines to the monitor the pointer
    is on: on a multi-monitor virtual desktop, the lines should stop at that
    screen's edges rather than stripe across every output. Right/bottom edges
    are exclusive, matching :func:`window_at_point`.
    """
    for rx, ry, rw, rh in rects:
        if rx <= x < rx + rw and ry <= y < ry + rh:
            return (rx, ry, rw, rh)
    return None
