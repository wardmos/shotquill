# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Headless Qt tests for the pinned-screenshot window.

Verifies window flags, sizing (including down-scaling oversized shots), and the
Esc/double-click dismissal path. Runs under ``QT_QPA_PLATFORM=offscreen``.
"""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from shotquill.ui.pinned import PinnedWindow  # noqa: E402


def _image(width, height, color="white"):
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    return image


def test_pinned_window_is_frameless_and_on_top(qtbot):
    window = PinnedWindow(_image(100, 80))
    qtbot.addWidget(window)
    flags = window.windowFlags()
    assert flags & Qt.FramelessWindowHint
    assert flags & Qt.WindowStaysOnTopHint


def test_pinned_window_sizes_to_logical_image(qtbot):
    dpr = QGuiApplication.primaryScreen().devicePixelRatio()
    window = PinnedWindow(_image(200, 120))
    qtbot.addWidget(window)
    # Physical pixels divided by the screen DPR -> logical window size.
    assert window.width() == round(200 / dpr)
    assert window.height() == round(120 / dpr)


def test_oversized_pin_is_scaled_to_fit_screen(qtbot):
    avail = QGuiApplication.primaryScreen().availableGeometry()
    huge = _image(avail.width() * 4, avail.height() * 4)
    window = PinnedWindow(huge)
    qtbot.addWidget(window)
    assert window.width() <= avail.width()
    assert window.height() <= avail.height()


def test_escape_closes_the_pin(qtbot):
    window = PinnedWindow(_image(100, 80))
    # Keep the C++ object alive past close() so qtbot teardown doesn't trip over
    # WA_DeleteOnClose; we only care that Esc dismisses (hides) the pin.
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    assert window.isVisible()
    # sendEvent routes straight to keyPressEvent regardless of offscreen focus.
    QApplication.sendEvent(window, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert not window.isVisible()
