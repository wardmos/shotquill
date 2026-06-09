# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Qt-grab backend under the offscreen platform: geometry and contract checks.

Offscreen grabs may be blank, but sizes, strides, and the typed unsupported
errors are all real — the pixel-accurate run happens on X11/Xvfb in CI.
"""

from __future__ import annotations

import pytest

from shotquill import headless
from shotquill.capture.base import Rect

pytest.importorskip("PySide6")


@pytest.fixture
def capturer(qapp):
    from shotquill.capture.qtgrab import QtGrabCapturer

    return QtGrabCapturer()


def test_fullscreen_matches_virtual_geometry(capturer):
    from PySide6.QtGui import QGuiApplication

    virtual = QGuiApplication.screens()[0].virtualGeometry()
    result = capturer.capture_fullscreen()
    assert result.width >= virtual.width()  # >= because of devicePixelRatio
    assert result.height >= virtual.height()
    assert len(result.pixels) == result.width * result.height * 4


def test_region_is_cropped(capturer):
    result = capturer.capture_region(Rect(x=0, y=0, width=10, height=8))
    assert (result.width, result.height) == (int(10 * result.scale), int(8 * result.scale))
    assert len(result.pixels) == result.width * result.height * 4


def test_region_outside_desktop_rejected(capturer):
    with pytest.raises(ValueError):
        capturer.capture_region(Rect(x=99999, y=99999, width=10, height=10))


def test_fullscreen_reports_virtual_origin(capturer):
    from PySide6.QtGui import QGuiApplication

    virtual = QGuiApplication.screens()[0].virtualGeometry()
    result = capturer.capture_fullscreen()
    assert (result.origin_x, result.origin_y) == (virtual.x(), virtual.y())


def test_region_reports_its_origin(capturer):
    result = capturer.capture_region(Rect(x=7, y=5, width=10, height=8))
    assert (result.origin_x, result.origin_y) == (7, 5)


def test_window_operations_are_typed_unsupported(capturer):
    with pytest.raises(headless.CapabilityUnsupported):
        capturer.list_windows()
    with pytest.raises(headless.CapabilityUnsupported):
        capturer.capture_window(1)
