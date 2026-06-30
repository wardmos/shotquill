# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Headless tests for the per-screen smart-overlay path (macOS multi-display).

The brain (:class:`SmartOverlay`) holds all logic; the per-screen views just
translate input into virtual-desktop coords and forward it, and repaint when the
brain changes. These drive the views/controller directly (offscreen reports one
screen) and assert the coordinate plumbing, present routing, and lifecycle.
"""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QMouseEvent  # noqa: E402

from shotquill.ui import smart_overlay  # noqa: E402
from shotquill.ui.smart_overlay import (  # noqa: E402
    SmartOverlay,
    SmartOverlayController,
    present_overlay,
)


def _screenshot(width=200, height=100) -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    return image


def _brain(qtbot, geometry=None) -> SmartOverlay:
    if geometry is None:
        geometry = QRect(0, 0, 100, 50)
    brain = SmartOverlay(_screenshot(), geometry, [])
    brain.setAttribute(Qt.WA_DeleteOnClose, False)
    qtbot.addWidget(brain)
    return brain


def _mouse(event_type, x, y):
    pos = QPointF(x, y)
    return QMouseEvent(event_type, pos, pos, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)


def test_controller_makes_one_view_per_screen(qtbot):
    brain = _brain(qtbot)
    controller = SmartOverlayController(brain)
    assert len(controller._views) == len(QGuiApplication.screens())
    for view in controller._views:
        qtbot.addWidget(view)


def test_view_forwards_drag_to_brain_in_virtual_desktop_coords(qtbot):
    brain = _brain(qtbot)
    received = []
    brain.region_selected.connect(lambda image, rect: received.append((image.size(), rect)))
    controller = SmartOverlayController(brain)
    view = controller._views[0]
    qtbot.addWidget(view)

    view.mousePressEvent(_mouse(QEvent.MouseButtonPress, 10, 10))
    view.mouseMoveEvent(_mouse(QEvent.MouseMove, 40, 30))
    view.mouseReleaseEvent(_mouse(QEvent.MouseButtonRelease, 40, 30))

    assert len(received) == 1
    size, rect = received[0]
    # 30x20 logical selection at the 2x screenshot scale -> 60x40 native pixels,
    # and the rect stays in global logical coords.
    assert (size.width(), size.height()) == (60, 40)
    assert rect == QRect(10, 10, 30, 20)


def test_view_offset_translates_local_input_to_brain(qtbot):
    # A view whose screen sits at virtual-desktop (100, 200) must add that offset
    # to local input before handing it to the brain.
    brain = _brain(qtbot, geometry=QRect(0, 0, 400, 400))
    controller = SmartOverlayController(brain)
    view = controller._views[0]
    qtbot.addWidget(view)
    view._offset = QPointF(100, 200)

    view.mousePressEvent(_mouse(QEvent.MouseButtonPress, 5, 7))
    assert (brain._origin.x(), brain._origin.y()) == (105, 207)


def test_brain_change_repaints_every_view(qtbot):
    brain = _brain(qtbot)
    controller = SmartOverlayController(brain)
    painted = []
    for view in controller._views:
        qtbot.addWidget(view)
        view.update = lambda v=view: painted.append(v)  # type: ignore[method-assign]

    brain._refresh()
    assert len(painted) == len(controller._views)


def test_brain_dirty_repaint_maps_to_view_coords(qtbot):
    brain = _brain(qtbot, geometry=QRect(0, 0, 400, 400))
    controller = SmartOverlayController(brain)
    view = controller._views[0]
    qtbot.addWidget(view)
    view._offset = QPointF(100, 200)
    painted = []
    view.update = lambda rect=None: painted.append(rect)  # type: ignore[method-assign]

    brain._refresh(QRectF(110, 220, 20, 10))

    assert len(painted) == 1
    assert painted[0].boundingRect() == QRect(2, 12, 36, 26)
    assert painted[0].rectCount() == 1


def test_outcome_hides_views_then_destruction_clears_them(qtbot):
    brain = _brain(qtbot)
    controller = SmartOverlayController(brain)
    for view in controller._views:
        qtbot.addWidget(view)
        view.show()

    brain.fullscreen_selected.emit()
    assert controller._finished is True
    assert all(not v.isVisible() for v in controller._views)


def test_focus_loss_cancels_when_no_view_is_active(qtbot):
    # Focus left every one of our windows (a hot corner, another app) -> cancel,
    # mirroring the single-window overlay's changeEvent. The views are never
    # shown, so none report active.
    brain = _brain(qtbot)
    cancelled = []
    brain.cancelled.connect(lambda: cancelled.append(True))
    controller = SmartOverlayController(brain)
    for view in controller._views:
        qtbot.addWidget(view)

    controller._on_focus_change()
    qtbot.wait(10)  # let the deferred check run
    assert cancelled == [True]


def test_focus_loss_does_not_cancel_after_an_outcome(qtbot):
    # The editor opened on accept steals focus; that must not read as an abandon.
    brain = _brain(qtbot)
    cancelled = []
    brain.cancelled.connect(lambda: cancelled.append(True))
    controller = SmartOverlayController(brain)
    for view in controller._views:
        qtbot.addWidget(view)

    brain.fullscreen_selected.emit()  # -> _finish sets _finished
    controller._on_focus_change()
    qtbot.wait(10)
    assert cancelled == []


def test_focus_loss_does_not_cancel_once_brain_is_closing(qtbot):
    # Accept paths set _closed before emitting; a focus check queued in between
    # must not fire a second outcome.
    brain = _brain(qtbot)
    cancelled = []
    brain.cancelled.connect(lambda: cancelled.append(True))
    controller = SmartOverlayController(brain)
    for view in controller._views:
        qtbot.addWidget(view)

    brain._closed = True
    controller._on_focus_change()
    qtbot.wait(10)
    assert cancelled == []


def test_present_overlay_uses_single_window_off_macos(qtbot, monkeypatch):
    brain = _brain(qtbot)
    monkeypatch.setattr(smart_overlay.sys, "platform", "linux")
    calls = []
    monkeypatch.setattr(brain, "present", lambda: calls.append(True))

    present_overlay(brain, QGuiApplication.instance())

    assert calls == [True]
    assert not hasattr(brain, "_controller")


def test_present_overlay_builds_controller_on_single_display_macos(qtbot, monkeypatch):
    # macOS always takes the per-screen path, single display included, so the
    # overlay clears the menu bar there too.
    brain = _brain(qtbot)
    monkeypatch.setattr(smart_overlay.sys, "platform", "darwin")

    present_overlay(brain, QGuiApplication.instance())  # offscreen: one screen

    assert isinstance(brain._controller, SmartOverlayController)
    assert len(brain._controller._views) == 1
    for view in brain._controller._views:
        qtbot.addWidget(view)


def test_present_overlay_builds_controller_on_multi_display_macos(qtbot, monkeypatch):
    brain = _brain(qtbot)
    monkeypatch.setattr(smart_overlay.sys, "platform", "darwin")

    real_screen = QGuiApplication.screens()[0]

    class _TwoScreenApp:
        def screens(self):
            return [real_screen, real_screen]

    present_overlay(brain, _TwoScreenApp())

    assert isinstance(brain._controller, SmartOverlayController)
    for view in brain._controller._views:
        qtbot.addWidget(view)


def test_present_overlay_single_window_on_single_output_wayland(qtbot, monkeypatch):
    # One Wayland output: a single fullscreen surface already covers it, so the
    # single-window path is kept (no controller).
    brain = _brain(qtbot)
    monkeypatch.setattr(smart_overlay.sys, "platform", "linux")
    monkeypatch.setattr(smart_overlay, "_compositor_prefers_fullscreen", lambda: True)
    calls = []
    monkeypatch.setattr(brain, "present", lambda: calls.append(True))

    present_overlay(brain, QGuiApplication.instance())  # offscreen: one screen

    assert calls == [True]
    assert not hasattr(brain, "_controller")


def test_present_overlay_uses_fullscreen_views_on_multi_output_wayland(qtbot, monkeypatch):
    # Several Wayland outputs: each gets its own fullscreen view sharing the
    # brain, since one fullscreen surface only covers the output it lands on.
    brain = _brain(qtbot)
    monkeypatch.setattr(smart_overlay.sys, "platform", "linux")
    monkeypatch.setattr(smart_overlay, "_compositor_prefers_fullscreen", lambda: True)

    real_screen = QGuiApplication.screens()[0]

    class _TwoScreenApp:
        def screens(self):
            return [real_screen, real_screen]

    present_overlay(brain, _TwoScreenApp())

    # The controller always builds one view per real QScreen (offscreen reports
    # one); what this asserts is that the Wayland multi-output branch was taken
    # and its views present themselves fullscreen rather than as plain windows.
    assert isinstance(brain._controller, SmartOverlayController)
    assert brain._controller._views
    assert all(view._fullscreen for view in brain._controller._views)
    for view in brain._controller._views:
        qtbot.addWidget(view)
