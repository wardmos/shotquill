# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for converting raw capture pixels into a QImage."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QImage  # noqa: E402

from shotquill.capture.base import CaptureResult  # noqa: E402
from shotquill.imaging import result_to_qimage  # noqa: E402


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
