# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Pure geometry helpers for annotation items (no Qt dependency, unit-testable)."""

from __future__ import annotations

import math


def arrowhead_points(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    length: float = 18.0,
    spread: float = 0.5,
) -> list[tuple[float, float]]:
    """Return the two barb points of an arrowhead at ``(x2, y2)``.

    The arrow points from ``(x1, y1)`` toward ``(x2, y2)``. ``length`` is the
    barb length in pixels; ``spread`` is the half-angle in radians.
    """
    angle = math.atan2(y2 - y1, x2 - x1)
    left = (
        x2 - length * math.cos(angle - spread),
        y2 - length * math.sin(angle - spread),
    )
    right = (
        x2 - length * math.cos(angle + spread),
        y2 - length * math.sin(angle + spread),
    )
    return [left, right]
