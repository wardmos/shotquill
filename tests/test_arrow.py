# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
import math

from shotquill.ui.items.geometry import arrowhead_points


def test_arrowhead_symmetric_along_x_axis():
    left, right = arrowhead_points(0.0, 0.0, 10.0, 0.0, length=18.0, spread=0.5)
    # Barbs sit behind the tip (smaller x) and mirror across the x-axis.
    assert left[0] < 10.0
    assert right[0] < 10.0
    assert math.isclose(left[0], right[0], abs_tol=1e-9)
    assert math.isclose(left[1], -right[1], abs_tol=1e-9)


def test_arrowhead_behind_tip_along_y_axis():
    left, right = arrowhead_points(0.0, 0.0, 0.0, 10.0, length=18.0, spread=0.5)
    # Arrow pointing down (+y): barbs are above the tip (smaller y).
    assert left[1] < 10.0
    assert right[1] < 10.0
    assert math.isclose(left[1], right[1], abs_tol=1e-9)
    assert math.isclose(left[0], -right[0], abs_tol=1e-9)
