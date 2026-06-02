# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Headless tests for the region-selection overlay.

Drives the overlay through its event handlers (rather than real OS mouse input,
which is unreliable for a frameless full-screen widget under offscreen Qt) and
asserts the emitted crop geometry, accounting for the native/logical scale ratio.
"""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent  # noqa: E402

from shotquill.ui.overlay import RegionOverlay  # noqa: E402


def _screenshot(width=200, height=100, color="white") -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    return image


def _overlay(qtbot, native=(200, 100), logical=(100, 50)):
    # Native screenshot is 2x the logical geometry -> sx = sy = 2.0.
    overlay = RegionOverlay(_screenshot(*native), QRect(0, 0, *logical))
    overlay.setAttribute(Qt.WA_DeleteOnClose, False)
    qtbot.addWidget(overlay)
    return overlay


def _mouse(event_type, x, y, button, buttons):
    pos = QPointF(x, y)
    # Use the (local, global, ...) form; the shorter overload is deprecated.
    return QMouseEvent(event_type, pos, pos, button, buttons, Qt.NoModifier)


def _press(overlay, x, y):
    overlay.mousePressEvent(_mouse(QEvent.MouseButtonPress, x, y, Qt.LeftButton, Qt.LeftButton))


def _move(overlay, x, y):
    overlay.mouseMoveEvent(_mouse(QEvent.MouseMove, x, y, Qt.NoButton, Qt.LeftButton))


def _release(overlay, x, y):
    overlay.mouseReleaseEvent(_mouse(QEvent.MouseButtonRelease, x, y, Qt.LeftButton, Qt.LeftButton))


def test_drag_emits_crop_scaled_to_native_resolution(qtbot):
    overlay = _overlay(qtbot)
    received = []
    overlay.region_selected.connect(received.append)

    _press(overlay, 10, 10)
    _move(overlay, 40, 30)
    _release(overlay, 40, 30)

    assert len(received) == 1
    cropped = received[0]
    # Logical 30x20 selection -> native 60x40 at the 2x scale.
    assert (cropped.width(), cropped.height()) == (60, 40)


def test_reverse_drag_still_emits_positive_crop(qtbot):
    overlay = _overlay(qtbot)
    received = []
    overlay.region_selected.connect(received.append)

    _press(overlay, 40, 30)
    _move(overlay, 10, 10)
    _release(overlay, 10, 10)

    assert len(received) == 1
    assert (received[0].width(), received[0].height()) == (60, 40)


def test_tiny_selection_cancels_instead_of_emitting(qtbot):
    overlay = _overlay(qtbot)
    selected = []
    cancelled = []
    overlay.region_selected.connect(selected.append)
    overlay.cancelled.connect(lambda: cancelled.append(True))

    _press(overlay, 10, 10)
    _release(overlay, 10, 10)  # zero-size selection

    assert selected == []
    assert cancelled == [True]


def test_escape_cancels(qtbot):
    overlay = _overlay(qtbot)
    cancelled = []
    overlay.cancelled.connect(lambda: cancelled.append(True))
    overlay.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert cancelled == [True]


def test_enter_accepts_current_selection(qtbot):
    overlay = _overlay(qtbot)
    received = []
    overlay.region_selected.connect(received.append)
    _press(overlay, 10, 10)
    _move(overlay, 40, 30)
    overlay.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
    assert len(received) == 1


def test_paint_before_drag_does_not_crash(qtbot):
    overlay = _overlay(qtbot)
    overlay.resize(100, 50)
    overlay.repaint()  # no origin/current yet -> early return in paintEvent
    assert overlay._origin is None
