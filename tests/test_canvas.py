# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Headless Qt tests for the annotation canvas.

Exercises the real drawing path (pens, item creation, undo stack, render) so
that runtime Qt enum/API mistakes surface in CI rather than on a Mac. Requires
PySide6 + pytest-qt; runs under ``QT_QPA_PLATFORM=offscreen``.
"""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QColor, QPixmap  # noqa: E402

from shotquill.ui.canvas import AnnotationCanvas  # noqa: E402
from shotquill.ui.tools import Tool  # noqa: E402


def _canvas(qtbot, width=120, height=90):
    pixmap = QPixmap(width, height)
    pixmap.fill(QColor("white"))
    canvas = AnnotationCanvas(pixmap)
    canvas.resize(width, height)
    qtbot.addWidget(canvas)
    return canvas


def test_export_matches_background_size(qtbot):
    canvas = _canvas(qtbot, 120, 90)
    image = canvas.export_image()
    assert (image.width(), image.height()) == (120, 90)


def test_drawing_a_rectangle_pushes_one_undo_command(qtbot):
    canvas = _canvas(qtbot)
    canvas.set_tool(Tool.RECT)
    viewport = canvas.viewport()
    qtbot.mousePress(viewport, Qt.LeftButton, pos=QPoint(15, 15))
    qtbot.mouseMove(viewport, pos=QPoint(80, 60))
    qtbot.mouseRelease(viewport, Qt.LeftButton, pos=QPoint(80, 60))
    assert canvas.undo_stack().count() == 1


def test_tiny_click_is_discarded(qtbot):
    canvas = _canvas(qtbot)
    canvas.set_tool(Tool.RECT)
    viewport = canvas.viewport()
    qtbot.mousePress(viewport, Qt.LeftButton, pos=QPoint(20, 20))
    qtbot.mouseRelease(viewport, Qt.LeftButton, pos=QPoint(20, 20))
    assert canvas.undo_stack().count() == 0
