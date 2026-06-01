# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
from shotquill.ui.geometry import scale_rect, selection_rect


def test_selection_rect_normalizes_reverse_drag():
    # Dragging up-left from (10,10) to (4,2) still yields a positive rect.
    assert selection_rect(10, 10, 4, 2) == (4, 2, 6, 8)


def test_selection_rect_forward_drag():
    assert selection_rect(0, 0, 5, 3) == (0, 0, 5, 3)


def test_scale_rect_applies_device_pixel_ratio():
    assert scale_rect((1, 2, 3, 4), 2.0, 2.0) == (2, 4, 6, 8)


def test_scale_rect_rounds():
    assert scale_rect((0, 0, 1, 1), 1.5, 1.5) == (0, 0, 2, 2)
