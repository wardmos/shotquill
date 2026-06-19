# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for converting raw capture pixels into a QImage."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import (
    QColor,  # noqa: E402
    QImage,  # noqa: E402
)

from shotquill.capture.base import CaptureResult  # noqa: E402
from shotquill.imaging import (  # noqa: E402
    changed_bbox,
    downscale_to_max,
    frame_diff_fraction,
    result_to_qimage,
)


def _result(width=2, height=2, color=(255, 0, 0, 255), premultiplied=False) -> CaptureResult:
    pixels = bytes(list(color) * (width * height))
    return CaptureResult(
        width=width, height=height, scale=1.0, pixels=pixels, premultiplied=premultiplied
    )


def test_result_to_qimage_dimensions_and_format():
    image = result_to_qimage(_result(4, 3))
    assert (image.width(), image.height()) == (4, 3)
    assert image.format() == QImage.Format.Format_RGBA8888


def test_result_to_qimage_premultiplied_uses_premultiplied_format():
    # Window captures arrive premultiplied; interpreting them as straight RGBA
    # would wash out semi-transparent pixels (e.g. rounded window corners).
    # Half-transparent red premultiplied is (128, 0, 0, 128) and must read back
    # as straight (255, 0, 0, 128).
    image = result_to_qimage(_result(2, 2, color=(128, 0, 0, 128), premultiplied=True))
    assert image.format() == QImage.Format.Format_RGBA8888_Premultiplied
    pixel = image.pixelColor(0, 0)
    assert (pixel.red(), pixel.green(), pixel.blue(), pixel.alpha()) == (255, 0, 0, 128)


def test_result_to_qimage_preserves_pixel_values():
    image = result_to_qimage(_result(2, 2, color=(10, 20, 30, 255)))
    pixel = image.pixelColor(0, 0)
    assert (pixel.red(), pixel.green(), pixel.blue()) == (10, 20, 30)


def test_result_to_qimage_pixel_positions_not_transposed():
    # A 3×1 strip with three distinct colors: any width/height or row-stride
    # mix-up would land the colors in the wrong place.
    pixels = bytes((255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255))
    result = CaptureResult(width=3, height=1, scale=1.0, pixels=pixels, premultiplied=False)
    image = result_to_qimage(result)
    assert image.pixelColor(0, 0).red() == 255
    assert image.pixelColor(1, 0).green() == 255
    assert image.pixelColor(2, 0).blue() == 255


def test_result_to_qimage_rejects_short_buffer():
    # QImage would read height*width*4 bytes with no bounds check; a buffer
    # short of that must be caught here rather than reading out of bounds.
    short = CaptureResult(width=4, height=4, scale=1.0, pixels=b"\x00" * 16, premultiplied=False)
    with pytest.raises(ValueError, match="buffer"):
        result_to_qimage(short)


def test_result_to_qimage_rejects_non_positive_size():
    empty = CaptureResult(width=0, height=4, scale=1.0, pixels=b"", premultiplied=False)
    with pytest.raises(ValueError, match="non-positive"):
        result_to_qimage(empty)


def test_result_to_qimage_is_detached_from_source_bytes():
    # The returned image must own its memory (a .copy()), so mutating/freeing the
    # source bytes can't corrupt it. We assert the buffer is independent.
    result = _result(2, 2)
    image = result_to_qimage(result)
    del result
    # Touching every pixel after dropping the source would crash on a dangling view.
    assert image.pixelColor(1, 1).alpha() == 255


def _solid(width: int, height: int) -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(0xFF0000FF)
    return image


def test_downscale_caps_the_longer_edge_and_keeps_aspect():
    # 400x200 capped to 100 -> longer edge becomes 100, aspect preserved (100x50).
    out = downscale_to_max(_solid(400, 200), 100)
    assert (out.width(), out.height()) == (100, 50)


def test_downscale_is_a_noop_when_within_the_cap():
    image = _solid(80, 60)
    out = downscale_to_max(image, 100)
    assert (out.width(), out.height()) == (80, 60)


def test_downscale_never_upscales():
    out = downscale_to_max(_solid(40, 30), 1000)
    assert (out.width(), out.height()) == (40, 30)


def test_downscale_disabled_when_max_is_non_positive():
    image = _solid(400, 200)
    for cap in (0, -1):
        out = downscale_to_max(image, cap)
        assert (out.width(), out.height()) == (400, 200)


# --- before/after diff (changed_bbox is pure; frame_diff_fraction uses Qt) ----


def _rgba(width, height, color=(0, 0, 0, 255)):
    return bytearray(list(color) * (width * height))


def _set(buf, width, x, y, color):
    i = (y * width + x) * 4
    buf[i : i + 4] = bytes(color)


def test_changed_bbox_locates_a_single_changed_pixel():
    w = h = 3
    before = _rgba(w, h)
    after = _rgba(w, h)
    _set(after, w, 2, 1, (255, 255, 255, 255))
    assert changed_bbox(bytes(before), bytes(after), w, h, threshold=16) == (2, 1, 3, 2)


def test_changed_bbox_spans_a_region():
    w = h = 4
    before = _rgba(w, h)
    after = _rgba(w, h)
    for x in (1, 2):
        for y in (1, 2, 3):
            _set(after, w, x, y, (200, 0, 0, 255))
    # x0,y0 inclusive .. x1,y1 exclusive → covers cols 1..2, rows 1..3.
    assert changed_bbox(bytes(before), bytes(after), w, h, threshold=16) == (1, 1, 3, 4)


def test_changed_bbox_none_when_identical_or_within_threshold():
    w = h = 2
    before = _rgba(w, h, (10, 10, 10, 255))
    assert changed_bbox(bytes(before), bytes(before), w, h) is None
    near = _rgba(w, h, (10, 10, 10, 255))
    _set(near, w, 0, 0, (20, 10, 10, 255))  # delta 10 <= threshold 16
    assert changed_bbox(bytes(before), bytes(near), w, h, threshold=16) is None


def test_changed_bbox_none_when_buffer_too_small():
    assert changed_bbox(b"\x00\x00\x00\xff", b"", 2, 2) is None


def test_frame_diff_fraction_returns_box_in_frame_fractions():
    before = QImage(40, 20, QImage.Format.Format_RGBA8888)
    before.fill(QColor(0, 0, 0))
    after = QImage(40, 20, QImage.Format.Format_RGBA8888)
    after.fill(QColor(0, 0, 0))
    # Change the bottom-right quadrant (x 20..40, y 10..20).
    for x in range(20, 40):
        for y in range(10, 20):
            after.setPixelColor(x, y, QColor(255, 255, 255))
    frac = frame_diff_fraction(before, after)
    assert frac is not None
    fx, fy, fw, fh = frac
    # Box sits in the bottom-right (allow slack for the coarse work-size grid).
    assert fx > 0.4 and fy > 0.4
    assert fx + fw <= 1.001 and fy + fh <= 1.001


def test_frame_diff_fraction_none_for_identical_frames():
    img = QImage(30, 30, QImage.Format.Format_RGBA8888)
    img.fill(QColor(5, 5, 5))
    assert frame_diff_fraction(img, img) is None


def test_frame_diff_fraction_none_on_size_mismatch():
    a = QImage(40, 20, QImage.Format.Format_RGBA8888)
    a.fill(QColor(0, 0, 0))
    b = QImage(20, 40, QImage.Format.Format_RGBA8888)  # different aspect → diff sizes
    b.fill(QColor(0, 0, 0))
    assert frame_diff_fraction(a, b) is None
