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
    # The view must be shown and laid out so mapToScene yields in-bounds scene
    # coordinates; otherwise simulated mouse points map outside the image.
    canvas.show()
    qtbot.waitExposed(canvas)
    canvas.fitInView(canvas.sceneRect(), Qt.KeepAspectRatio)
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


def test_drawing_a_mosaic_pushes_one_undo_command(qtbot):
    canvas = _canvas(qtbot)
    canvas.set_tool(Tool.MOSAIC)
    viewport = canvas.viewport()
    qtbot.mousePress(viewport, Qt.LeftButton, pos=QPoint(15, 15))
    qtbot.mouseMove(viewport, pos=QPoint(80, 60))
    qtbot.mouseRelease(viewport, Qt.LeftButton, pos=QPoint(80, 60))
    assert canvas.undo_stack().count() == 1


def test_pixelate_preserves_dimensions():
    from shotquill.ui.items.mosaic import pixelate

    source = QPixmap(40, 30)
    source.fill(QColor("red"))
    out = pixelate(source, 8)
    assert (out.width(), out.height()) == (40, 30)


@pytest.mark.parametrize(
    "tool",
    [Tool.ELLIPSE, Tool.LINE, Tool.ARROW, Tool.PEN, Tool.HIGHLIGHTER],
)
def test_each_drag_tool_pushes_one_undo_command(qtbot, tool):
    canvas = _canvas(qtbot)
    canvas.set_tool(tool)
    viewport = canvas.viewport()
    qtbot.mousePress(viewport, Qt.LeftButton, pos=QPoint(15, 15))
    qtbot.mouseMove(viewport, pos=QPoint(40, 35))
    qtbot.mouseMove(viewport, pos=QPoint(80, 60))
    qtbot.mouseRelease(viewport, Qt.LeftButton, pos=QPoint(80, 60))
    assert canvas.undo_stack().count() == 1


def test_undo_then_redo_toggles_scene_membership(qtbot):
    canvas = _canvas(qtbot)
    canvas.set_tool(Tool.RECT)
    viewport = canvas.viewport()
    qtbot.mousePress(viewport, Qt.LeftButton, pos=QPoint(15, 15))
    qtbot.mouseMove(viewport, pos=QPoint(80, 60))
    qtbot.mouseRelease(viewport, Qt.LeftButton, pos=QPoint(80, 60))
    stack = canvas.undo_stack()
    assert stack.canUndo()
    stack.undo()
    assert stack.index() == 0
    assert stack.canRedo()
    stack.redo()
    assert stack.index() == 1


def test_color_and_width_setters_round_trip(qtbot):
    canvas = _canvas(qtbot)
    canvas.set_color(QColor("blue"))
    canvas.set_width(7)
    assert canvas.color() == QColor("blue")
    assert canvas.width() == 7


def test_width_is_clamped_to_minimum_one(qtbot):
    canvas = _canvas(qtbot)
    canvas.set_width(0)
    assert canvas.width() == 1


def test_select_tool_does_not_create_items(qtbot):
    canvas = _canvas(qtbot)
    canvas.set_tool(Tool.SELECT)
    viewport = canvas.viewport()
    qtbot.mousePress(viewport, Qt.LeftButton, pos=QPoint(15, 15))
    qtbot.mouseMove(viewport, pos=QPoint(80, 60))
    qtbot.mouseRelease(viewport, Qt.LeftButton, pos=QPoint(80, 60))
    assert canvas.undo_stack().count() == 0


def test_background_image_is_unannotated(qtbot):
    canvas = _canvas(qtbot, 120, 90)
    bg = canvas.background_image()
    assert (bg.width(), bg.height()) == (120, 90)


def test_export_after_drawing_keeps_background_size(qtbot):
    canvas = _canvas(qtbot, 120, 90)
    canvas.set_tool(Tool.RECT)
    viewport = canvas.viewport()
    qtbot.mousePress(viewport, Qt.LeftButton, pos=QPoint(15, 15))
    qtbot.mouseMove(viewport, pos=QPoint(80, 60))
    qtbot.mouseRelease(viewport, Qt.LeftButton, pos=QPoint(80, 60))
    image = canvas.export_image()
    assert (image.width(), image.height()) == (120, 90)


def test_text_tool_creates_item_on_single_click(qtbot):
    canvas = _canvas(qtbot)
    canvas.set_tool(Tool.TEXT)
    viewport = canvas.viewport()
    # Text is placed on press (no drag needed) and is immediately undoable.
    qtbot.mousePress(viewport, Qt.LeftButton, pos=QPoint(30, 30))
    qtbot.mouseRelease(viewport, Qt.LeftButton, pos=QPoint(30, 30))
    assert canvas.undo_stack().count() == 1
