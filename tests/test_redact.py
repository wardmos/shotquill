# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Solid-block redaction maths and pixel fill (no real screen needed)."""

from __future__ import annotations

from shotquill import redact
from shotquill.capture.base import CaptureResult, Rect


def _solid(width, height, value=200):
    return CaptureResult(
        width=width,
        height=height,
        scale=1.0,
        pixels=bytes([value, value, value, 255] * width * height),
    )


def _px(result, x, y):
    i = (y * result.width + x) * 4
    return tuple(result.pixels[i : i + 4])


# --- geometry ---------------------------------------------------------------


def test_pixel_rect_basic():
    assert redact.pixel_rect(10, 10, 1.0, (0, 0), Rect(2, 3, 4, 5)) == (2, 3, 6, 8)


def test_pixel_rect_scales():
    # Retina: logical bounds double into physical pixels.
    assert redact.pixel_rect(20, 20, 2.0, (0, 0), Rect(1, 1, 3, 4)) == (2, 2, 8, 10)


def test_pixel_rect_subtracts_origin():
    # Region capture: the image's top-left is the region origin.
    assert redact.pixel_rect(100, 100, 1.0, (10, 20), Rect(15, 25, 5, 5)) == (5, 5, 10, 10)


def test_pixel_rect_clips_to_image():
    assert redact.pixel_rect(8, 8, 1.0, (0, 0), Rect(-5, -5, 100, 100)) == (0, 0, 8, 8)


def test_pixel_rect_off_screen_is_none():
    assert redact.pixel_rect(8, 8, 1.0, (0, 0), Rect(50, 50, 4, 4)) is None


def test_pixel_rect_rounds_outward():
    # 1.5 floors to 1 at the top-left and ceils to 4 at the bottom-right, so the
    # block always fully covers the window instead of leaving a seam.
    assert redact.pixel_rect(10, 10, 1.5, (0, 0), Rect(1, 1, 1, 1)) == (1, 1, 3, 3)


# --- fill -------------------------------------------------------------------


def test_fill_blacks_out_the_rect_and_leaves_the_rest():
    result = _solid(4, 4)
    out = redact.fill_rects(result, [(1, 1, 3, 3)])
    assert _px(out, 1, 1) == (0, 0, 0, 255)
    assert _px(out, 2, 2) == (0, 0, 0, 255)
    assert _px(out, 0, 0) == (200, 200, 200, 255)
    assert _px(out, 3, 3) == (200, 200, 200, 255)
    # The original is untouched (a copy is returned).
    assert _px(result, 1, 1) == (200, 200, 200, 255)


def test_rect_intersects():
    a = Rect(0, 0, 10, 10)
    assert redact.rect_intersects(a, Rect(5, 5, 10, 10))  # overlapping corner
    assert redact.rect_intersects(a, Rect(-5, -5, 8, 8))  # overlapping from top-left
    assert not redact.rect_intersects(a, Rect(10, 0, 5, 5))  # touching edge only
    assert not redact.rect_intersects(a, Rect(20, 20, 5, 5))  # disjoint


def test_fill_clamps_out_of_bounds_rect_to_the_buffer():
    # A rect wider/taller than the result must not let a row-spanning write
    # bleed fill bytes into the wrong scanline — fill_rects clamps defensively
    # rather than trusting the caller (security-critical redaction path).
    result = _solid(4, 4)
    out = redact.fill_rects(result, [(2, 2, 100, 100)])
    # The in-bounds part of the rect is filled, nothing outside it is touched,
    # and the buffer keeps its exact length (no overflow).
    assert len(out.pixels) == len(result.pixels)
    assert _px(out, 3, 3) == (0, 0, 0, 255)
    assert _px(out, 0, 0) == (200, 200, 200, 255)


def test_redact_bounds_counts_only_what_lands_in_frame():
    result = _solid(8, 8)
    on = Rect(0, 0, 2, 2)
    off = Rect(100, 100, 2, 2)
    out, count = redact.redact_bounds(result, (0, 0), [on, off])
    assert count == 1
    assert _px(out, 0, 0) == (0, 0, 0, 255)


def test_redact_bounds_no_overlap_returns_original_unchanged():
    result = _solid(8, 8)
    out, count = redact.redact_bounds(result, (0, 0), [Rect(100, 100, 2, 2)])
    assert count == 0
    assert out is result
