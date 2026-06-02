# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for converting raw capture pixels into a QImage."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QImage  # noqa: E402

from shotquill.capture.base import CaptureResult  # noqa: E402
from shotquill.imaging import result_to_qimage  # noqa: E402


def _result(width=2, height=2, color=(255, 0, 0, 255)) -> CaptureResult:
    pixels = bytes(list(color) * (width * height))
    return CaptureResult(width=width, height=height, scale=1.0, pixels=pixels)


def test_result_to_qimage_dimensions_and_format():
    image = result_to_qimage(_result(4, 3))
    assert (image.width(), image.height()) == (4, 3)
    assert image.format() == QImage.Format.Format_RGBA8888


def test_result_to_qimage_preserves_pixel_values():
    image = result_to_qimage(_result(2, 2, color=(10, 20, 30, 255)))
    pixel = image.pixelColor(0, 0)
    assert (pixel.red(), pixel.green(), pixel.blue()) == (10, 20, 30)


def test_result_to_qimage_is_detached_from_source_bytes():
    # The returned image must own its memory (a .copy()), so mutating/freeing the
    # source bytes can't corrupt it. We assert the buffer is independent.
    result = _result(2, 2)
    image = result_to_qimage(result)
    del result
    # Touching every pixel after dropping the source would crash on a dangling view.
    assert image.pixelColor(1, 1).alpha() == 255
