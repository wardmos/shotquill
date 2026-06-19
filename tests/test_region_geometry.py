# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
from shotquill.ui.geometry import (
    crop_edge_hits,
    loupe_anchor,
    move_rect_within,
    rect_containing,
    resize_selection,
    scale_rect,
    selection_rect,
    window_at_point,
)


def test_selection_rect_normalizes_reverse_drag():
    # Dragging up-left from (10,10) to (4,2) still yields a positive rect.
    assert selection_rect(10, 10, 4, 2) == (4, 2, 6, 8)


def test_selection_rect_forward_drag():
    assert selection_rect(0, 0, 5, 3) == (0, 0, 5, 3)


def test_scale_rect_applies_device_pixel_ratio():
    assert scale_rect((1, 2, 3, 4), 2.0, 2.0) == (2, 4, 6, 8)


def test_scale_rect_rounds():
    assert scale_rect((0, 0, 1, 1), 1.5, 1.5) == (0, 0, 2, 2)


def test_loupe_anchor_sits_below_right_of_pointer():
    assert loupe_anchor(100, 100, 120, 110, 1000, 800, 20) == (120, 120)


def test_loupe_anchor_flips_left_near_right_edge():
    # 950 + 20 + 120 would overflow the 1000-wide bound -> flip to the left.
    assert loupe_anchor(950, 100, 120, 110, 1000, 800, 20) == (950 - 20 - 120, 120)


def test_loupe_anchor_flips_up_near_bottom_edge():
    assert loupe_anchor(100, 780, 120, 110, 1000, 800, 20) == (120, 780 - 20 - 110)


def test_loupe_anchor_axes_flip_independently():
    # Bottom-right corner: both axes flip.
    assert loupe_anchor(990, 790, 120, 110, 1000, 800, 20) == (990 - 140, 790 - 130)


def test_loupe_anchor_clamps_to_origin_when_bounds_too_small():
    # A bound smaller than the loupe on both sides: never go negative.
    assert loupe_anchor(5, 5, 120, 110, 100, 100, 20) == (0, 0)


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


# Two side-by-side monitors on a virtual desktop (overlay-local coords).
_MONITORS = [(0, 0, 100, 100), (100, 0, 120, 90)]


def test_rect_containing_picks_the_monitor_under_the_pointer():
    assert rect_containing(_MONITORS, 50, 50) == (0, 0, 100, 100)
    assert rect_containing(_MONITORS, 150, 40) == (100, 0, 120, 90)


def test_rect_containing_returns_none_in_the_gap_between_monitors():
    # Below the shorter right-hand monitor: inside no screen.
    assert rect_containing(_MONITORS, 150, 95) is None


def test_rect_containing_right_and_bottom_edges_exclusive():
    assert rect_containing([(0, 0, 100, 100)], 100, 50) is None
    assert rect_containing([(0, 0, 100, 100)], 50, 100) is None
    assert rect_containing([(0, 0, 100, 100)], 0, 0) == (0, 0, 100, 100)


def test_scale_rect_edges_matches_scale_rect_on_integer_products():
    from shotquill.ui.geometry import scale_rect_edges

    assert scale_rect_edges((1, 2, 3, 4), 2.0, 2.0) == (2, 4, 6, 8)


def test_scale_rect_edges_covers_full_selection_under_fractional_scale():
    from shotquill.ui.geometry import scale_rect_edges

    # 1.5x: logical (1,1,2,2) spans physical 1.5..4.5; rounding x and width
    # separately would give (2,2,3,3) and clip the edges. Edge conversion
    # floors/ceils to fully cover: 1..5 -> (1,1,4,4).
    assert scale_rect_edges((1, 1, 2, 2), 1.5, 1.5) == (1, 1, 4, 4)


def test_scale_rect_edges_never_smaller_than_logical_span():
    import itertools

    from shotquill.ui.geometry import scale_rect_edges

    for x, w, s in itertools.product((0, 1, 3, 7), (1, 2, 5), (1.0, 1.25, 1.5, 2.0)):
        px, _, pw, _ = scale_rect_edges((x, 0, w, 1), s, 1.0)
        assert px <= x * s
        assert px + pw >= (x + w) * s


# --- crop_edge_hits: which edges of a box the pointer grabs ------------------

# A 100x80 box; pointer coords are relative to its top-left.


def test_crop_edge_hits_each_side():
    assert crop_edge_hits(0, 40, 100, 80, 8) == (True, False, False, False)
    assert crop_edge_hits(100, 40, 100, 80, 8) == (False, False, True, False)
    assert crop_edge_hits(50, 0, 100, 80, 8) == (False, True, False, False)
    assert crop_edge_hits(50, 80, 100, 80, 8) == (False, False, False, True)


def test_crop_edge_hits_corner_lights_two_edges():
    # Top-left corner grabs left and top together (a diagonal resize).
    assert crop_edge_hits(0, 0, 100, 80, 8) == (True, True, False, False)
    # Bottom-right corner grabs right and bottom.
    assert crop_edge_hits(100, 80, 100, 80, 8) == (False, False, True, True)


def test_crop_edge_hits_center_and_far_pointer_grab_nothing():
    assert crop_edge_hits(50, 40, 100, 80, 8) == (False, False, False, False)
    # Well outside the box on every side: no grab.
    assert crop_edge_hits(-40, 40, 100, 80, 8) == (False, False, False, False)
    assert crop_edge_hits(140, 40, 100, 80, 8) == (False, False, False, False)


def test_crop_edge_hits_band_straddles_the_edge():
    # The band reaches a little OUTSIDE the box too, so a handle drawn on the
    # edge is easy to catch from either side (the overlay has no window frame).
    assert crop_edge_hits(-5, 40, 100, 80, 8) == (True, False, False, False)
    assert crop_edge_hits(105, 40, 100, 80, 8) == (False, False, True, False)
    assert crop_edge_hits(50, -5, 100, 80, 8) == (False, True, False, False)


def test_crop_edge_hits_thin_box_keeps_only_the_nearer_edge():
    # Box narrower than 2*margin: a pointer left-of-center grabs the left edge
    # alone, never both opposite edges at once.
    assert crop_edge_hits(4, 40, 10, 80, 8) == (True, False, False, False)
    assert crop_edge_hits(7, 40, 10, 80, 8) == (False, False, True, False)


# --- resize_selection: move active edges to the pointer ---------------------

# sel (10,10,30,20) -> edges left=10 top=10 right=40 bottom=30; bounds 0..100/0..80.
_SEL = (10.0, 10.0, 30.0, 20.0)
_BOUNDS = (0.0, 0.0, 100.0, 80.0)


def test_resize_selection_moves_one_edge():
    # Drag the right edge out to x=50: only width grows.
    grown = resize_selection(_SEL, (False, False, True, False), 50, 0, _BOUNDS, 2)
    assert grown == (10, 10, 40, 20)


def test_resize_selection_left_edge_expands_outward():
    # Drag the left edge to x=4: x shrinks, width grows.
    assert resize_selection(_SEL, (True, False, False, False), 4, 0, _BOUNDS, 2) == (4, 10, 36, 20)


def test_resize_selection_corner_moves_both_axes():
    # Top-left corner to (5,5): both x/y shrink, both w/h grow.
    assert resize_selection(_SEL, (True, True, False, False), 5, 5, _BOUNDS, 2) == (5, 5, 35, 25)


def test_resize_selection_clamps_to_bounds():
    # Drag the right edge far past the desktop edge: clamps to bounds width.
    assert resize_selection(_SEL, (False, False, True, False), 9999, 0, _BOUNDS, 2) == (
        10,
        10,
        90,
        20,
    )


def test_resize_selection_honours_min_size():
    # Drag the right edge inward past the left edge: floored at left + min_size.
    assert resize_selection(_SEL, (False, False, True, False), 0, 0, _BOUNDS, 2) == (10, 10, 2, 20)


# --- move_rect_within: clamp a moved selection back inside bounds ------------


def test_move_rect_within_leaves_an_inside_rect_untouched():
    assert move_rect_within((10, 10, 30, 20), _BOUNDS) == (10, 10, 30, 20)


def test_move_rect_within_clamps_past_right_and_bottom():
    # A box pushed off the bottom-right is shifted fully back inside.
    assert move_rect_within((90, 70, 30, 20), _BOUNDS) == (70, 60, 30, 20)


def test_move_rect_within_clamps_past_top_left():
    assert move_rect_within((-5, -5, 30, 20), _BOUNDS) == (0, 0, 30, 20)
