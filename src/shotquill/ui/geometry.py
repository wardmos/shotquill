# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Pure geometry helpers for the region overlay (no Qt dependency, unit-testable)."""

from __future__ import annotations


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
