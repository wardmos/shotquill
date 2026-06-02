# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
from shotquill.ui.geometry import scale_rect, selection_rect, window_at_point


def test_selection_rect_normalizes_reverse_drag():
    # Dragging up-left from (10,10) to (4,2) still yields a positive rect.
    assert selection_rect(10, 10, 4, 2) == (4, 2, 6, 8)


def test_selection_rect_forward_drag():
    assert selection_rect(0, 0, 5, 3) == (0, 0, 5, 3)


def test_scale_rect_applies_device_pixel_ratio():
    assert scale_rect((1, 2, 3, 4), 2.0, 2.0) == (2, 4, 6, 8)


def test_scale_rect_rounds():
    assert scale_rect((0, 0, 1, 1), 1.5, 1.5) == (0, 0, 2, 2)


_BOXES = [(0, 0, 100, 100), (50, 50, 100, 100)]


def test_window_at_point_picks_frontmost_on_overlap():
    # (75, 75) is inside both boxes; the front-most (index 0) wins.
    assert window_at_point(_BOXES, 75, 75) == 0


def test_window_at_point_finds_back_window_when_front_misses():
    assert window_at_point(_BOXES, 140, 140) == 1


def test_window_at_point_returns_none_outside_all():
    assert window_at_point(_BOXES, 300, 300) is None


def test_window_at_point_right_and_bottom_edges_exclusive():
    # The box spans [0,100); the far edge belongs to no window.
    assert window_at_point([(0, 0, 100, 100)], 100, 50) is None
    assert window_at_point([(0, 0, 100, 100)], 0, 0) == 0
