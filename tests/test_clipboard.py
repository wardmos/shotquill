# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the Qt clipboard helpers (image and text)."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QColor, QGuiApplication, QImage  # noqa: E402

from shotquill.capture.base import CaptureResult  # noqa: E402
from shotquill.output.clipboard import copy_image, copy_qimage, copy_text  # noqa: E402


def _image(width=3, height=2, color="blue") -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(QColor(color))
    return image


def test_copy_qimage_round_trips(qapp):
    copy_qimage(_image(4, 4, "green"))
    pasted = QGuiApplication.clipboard().image()
    assert (pasted.width(), pasted.height()) == (4, 4)
    assert pasted.pixelColor(0, 0).green() == 128 or pasted.pixelColor(0, 0).green() == 255


def test_copy_text_round_trips(qapp):
    copy_text("hello 世界")
    assert QGuiApplication.clipboard().text() == "hello 世界"


def test_copy_image_from_capture_result(qapp):
    pixels = bytes([255, 0, 0, 255] * 4)
    result = CaptureResult(width=2, height=2, scale=1.0, pixels=pixels)
    copy_image(result)
    pasted = QGuiApplication.clipboard().image()
    assert (pasted.width(), pasted.height()) == (2, 2)
