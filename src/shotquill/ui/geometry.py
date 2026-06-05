# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Pure geometry helpers for the overlays (no Qt dependency, unit-testable)."""

from __future__ import annotations

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
