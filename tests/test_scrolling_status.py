# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the visible long-screenshot progress HUD."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QRect, QSize, Qt  # noqa: E402

from shotquill.i18n import t  # noqa: E402
from shotquill.ui.scrolling_status import ScrollingStatus, status_geometry  # noqa: E402


def test_status_geometry_prefers_space_outside_the_capture_region():
    available = QRect(0, 0, 1000, 700)
    target = QRect(200, 160, 600, 400)

    geometry = status_geometry(target, QSize(360, 52), available)

    assert available.contains(geometry)
    assert not geometry.intersects(target)


def test_status_geometry_stays_on_screen_when_the_capture_fills_it():
    available = QRect(0, 0, 800, 600)

    geometry = status_geometry(available, QSize(360, 52), available)

    assert available.contains(geometry)
    assert geometry.intersects(available)


def test_status_instructs_manual_scroll_and_emits_finish(qapp, qtbot):
    status = ScrollingStatus(
        QRect(200, 160, 400, 300),
        available_geometry=QRect(0, 0, 1000, 700),
    )
    qtbot.addWidget(status)
    finished = []
    status.finish_requested.connect(lambda: finished.append(True))

    status.set_progress(7)
    status.present()

    assert status.isVisible()
    assert status.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert status.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert status._label.text() == t("scrolling.status").format(frames=7)
    rendered = status.grab().toImage()
    assert rendered.pixelColor(5, rendered.height() // 2).alpha() >= 200
    qtbot.mouseClick(status._finish_button, Qt.MouseButton.LeftButton)
    assert finished == [True]


def test_status_only_hides_during_capture_when_it_overlaps_target(qapp, qtbot):
    available = QRect(0, 0, 800, 600)
    outside = ScrollingStatus(
        QRect(200, 160, 400, 300),
        available_geometry=available,
    )
    qtbot.addWidget(outside)
    outside.present()
    assert outside.geometry().intersects(outside.target_geometry) is False
    assert outside.suspend_for_capture() is False
    assert outside.isVisible()

    overlapping = ScrollingStatus(available, available_geometry=available)
    qtbot.addWidget(overlapping)
    overlapping.present()
    assert overlapping.geometry().intersects(overlapping.target_geometry)
    assert overlapping.suspend_for_capture() is True
    assert not overlapping.isVisible()

    overlapping.resume_after_capture()
    assert overlapping.isVisible()
