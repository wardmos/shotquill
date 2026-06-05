# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Headless tests for the smart-capture overlay.

Drives the overlay through its event handlers (rather than real OS mouse input,
which is unreliable for a frameless full-screen widget under offscreen Qt) and
asserts which capture mode each gesture resolves to: drag -> region, click on a
hovered window -> that window, click on empty space -> full screen.
"""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent  # noqa: E402

from shotquill.capture.base import Rect, WindowInfo  # noqa: E402
from shotquill.ui.smart_overlay import SmartOverlay  # noqa: E402


def _screenshot(width=200, height=100, color="white") -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    return image


def _windows():
    # One window occupying the right half of a 100x50 logical desktop.
    return [WindowInfo(window_id=42, owner="Demo", title="Doc", bounds=Rect(50, 0, 50, 50))]


def _overlay(qtbot, native=(200, 100), logical=(100, 50), windows=None):
    # Native screenshot is 2x the logical geometry -> sx = sy = 2.0.
    overlay = SmartOverlay(
        _screenshot(*native), QRect(0, 0, *logical), windows if windows is not None else []
    )
    overlay.setAttribute(Qt.WA_DeleteOnClose, False)
    qtbot.addWidget(overlay)
    return overlay


def _mouse(event_type, x, y, button, buttons):
    pos = QPointF(x, y)
    # Use the (local, global, ...) form; the shorter overload is deprecated.
    return QMouseEvent(event_type, pos, pos, button, buttons, Qt.NoModifier)


def _press(overlay, x, y, button=Qt.LeftButton):
    overlay.mousePressEvent(_mouse(QEvent.MouseButtonPress, x, y, button, button))


def _move(overlay, x, y, buttons=Qt.NoButton):
    overlay.mouseMoveEvent(_mouse(QEvent.MouseMove, x, y, Qt.NoButton, buttons))


def _release(overlay, x, y):
    overlay.mouseReleaseEvent(_mouse(QEvent.MouseButtonRelease, x, y, Qt.LeftButton, Qt.LeftButton))


def test_drag_emits_region_crop_scaled_to_native(qtbot):
    overlay = _overlay(qtbot)
    received = []
    overlay.region_selected.connect(lambda image, rect: received.append((image, rect)))

    _press(overlay, 10, 10)
    _move(overlay, 40, 30, buttons=Qt.LeftButton)
    _release(overlay, 40, 30)

    assert len(received) == 1
    image, rect = received[0]
    # Logical 30x20 selection -> native 60x40 at the 2x scale.
    assert (image.width(), image.height()) == (60, 40)
    # The rect stays in logical, global coordinates so the editor can reopen
    # the shot in place.
    assert rect == QRect(10, 10, 30, 20)


def test_region_rect_is_translated_to_global_coordinates(qtbot):
    # A virtual desktop whose origin is not (0, 0) — e.g. a screen arranged to
    # the right of another. The emitted rect must be shifted back to global.
    overlay = SmartOverlay(_screenshot(), QRect(100, 200, 100, 50), [])
    overlay.setAttribute(Qt.WA_DeleteOnClose, False)
    qtbot.addWidget(overlay)
    received = []
    overlay.region_selected.connect(lambda image, rect: received.append(rect))

    _press(overlay, 10, 10)
    _move(overlay, 40, 30, buttons=Qt.LeftButton)
    _release(overlay, 40, 30)

    assert received == [QRect(110, 210, 30, 20)]


def test_click_on_empty_space_emits_fullscreen(qtbot):
    overlay = _overlay(qtbot, windows=_windows())
    fullscreen = []
    overlay.fullscreen_selected.connect(lambda: fullscreen.append(True))

    # Pointer sits in the empty left half, then a click with no drag.
    _move(overlay, 10, 10)
    _press(overlay, 10, 10)
    _release(overlay, 10, 10)

    assert fullscreen == [True]


def test_click_on_window_emits_its_id_and_bounds(qtbot):
    overlay = _overlay(qtbot, windows=_windows())
    received = []
    overlay.window_selected.connect(lambda window_id, rect: received.append((window_id, rect)))

    # Move onto the right-half window, then click without dragging.
    _move(overlay, 70, 25)
    _press(overlay, 70, 25)
    _release(overlay, 70, 25)

    assert received == [(42, QRect(50, 0, 50, 50))]


def test_tiny_move_counts_as_click_not_drag(qtbot):
    overlay = _overlay(qtbot, windows=_windows())
    region = []
    windows = []
    overlay.region_selected.connect(lambda image, rect: region.append((image, rect)))
    overlay.window_selected.connect(lambda window_id, rect: windows.append(window_id))

    _move(overlay, 70, 25)
    _press(overlay, 70, 25)
    _move(overlay, 71, 26, buttons=Qt.LeftButton)  # within the drag threshold
    _release(overlay, 71, 26)

    assert region == []
    assert windows == [42]


def test_right_click_cancels(qtbot):
    overlay = _overlay(qtbot)
    cancelled = []
    overlay.cancelled.connect(lambda: cancelled.append(True))
    _press(overlay, 10, 10, button=Qt.RightButton)
    assert cancelled == [True]


def test_escape_cancels(qtbot):
    overlay = _overlay(qtbot)
    cancelled = []
    overlay.cancelled.connect(lambda: cancelled.append(True))
    overlay.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert cancelled == [True]


def test_paint_before_interaction_does_not_crash(qtbot):
    overlay = _overlay(qtbot, windows=_windows())
    overlay.resize(100, 50)
    overlay.repaint()  # no hover, no drag -> full-screen hint path
    assert overlay._hover is None
    assert overlay._cursor is None  # no move yet -> no loupe


def test_move_tracks_cursor_for_loupe(qtbot):
    overlay = _overlay(qtbot, windows=_windows())
    _move(overlay, 70, 25)
    assert overlay._cursor is not None
    assert (overlay._cursor.x(), overlay._cursor.y()) == (70, 25)


def test_leave_hides_loupe(qtbot):
    overlay = _overlay(qtbot)
    _move(overlay, 10, 10)
    overlay.leaveEvent(QEvent(QEvent.Leave))
    assert overlay._cursor is None


def test_paint_with_loupe_does_not_crash(qtbot):
    overlay = _overlay(qtbot, windows=_windows())
    overlay.resize(100, 50)
    _move(overlay, 70, 25)  # hover path with loupe
    overlay.repaint()
    _press(overlay, 10, 10)
    _move(overlay, 40, 30, buttons=Qt.LeftButton)  # drag path with loupe
    overlay.repaint()


def test_paint_loupe_at_screen_corner_does_not_crash(qtbot):
    # At (0, 0) the magnified source patch extends past the screenshot and the
    # loupe placement clamps; both paths must stay valid.
    overlay = _overlay(qtbot)
    overlay.resize(100, 50)
    _move(overlay, 0, 0)
    overlay.repaint()
    _move(overlay, 99, 49)  # opposite corner -> anchor flips on both axes
    overlay.repaint()
