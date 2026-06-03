# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""The full-screen capture overlays must cancel when they lose focus.

A hot corner firing Mission Control / App Exposé (or Cmd-Tab, or a click
elsewhere) steals focus from the dimmed, screen-covering overlay. Without a
bail-out the user is trapped behind it — Esc only works while the overlay holds
keyboard focus. These tests drive the activation logic directly so they don't
depend on a real window manager. Runs under ``QT_QPA_PLATFORM=offscreen``.
"""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QRect  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402

from shotquill.capture.base import Rect, WindowInfo  # noqa: E402
from shotquill.ui.overlay import RegionOverlay  # noqa: E402
from shotquill.ui.window_picker import WindowPicker  # noqa: E402


def _image(w=200, h=200):
    image = QImage(w, h, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    return image


def _make(kind, qtbot):
    geometry = QRect(0, 0, 200, 200)
    if kind == "region":
        widget = RegionOverlay(_image(), geometry)
    else:
        windows = [WindowInfo(1, "App", "Title", Rect(0, 0, 100, 100))]
        widget = WindowPicker(_image(), geometry, windows)
    qtbot.addWidget(widget)
    return widget


def _activation_change(widget, *, active):
    widget.isActiveWindow = lambda: active  # type: ignore[method-assign]
    widget.changeEvent(QEvent(QEvent.Type.ActivationChange))


@pytest.mark.parametrize("kind", ["region", "window"])
def test_losing_focus_after_activation_cancels(kind, qtbot):
    widget = _make(kind, qtbot)
    cancelled = []
    widget.cancelled.connect(lambda: cancelled.append(True))

    _activation_change(widget, active=True)  # overlay becomes the key window
    assert cancelled == []  # ...still up

    _activation_change(widget, active=False)  # focus stolen (e.g. hot corner)
    assert cancelled == [True]


@pytest.mark.parametrize("kind", ["region", "window"])
def test_deactivation_before_activation_does_not_cancel(kind, qtbot):
    # A stray deactivation during show must not pull the rug out before the
    # overlay has ever held focus.
    widget = _make(kind, qtbot)
    cancelled = []
    widget.cancelled.connect(lambda: cancelled.append(True))

    _activation_change(widget, active=False)
    assert cancelled == []
