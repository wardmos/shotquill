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


def test_right_release_mid_drag_does_not_commit_the_item(qtbot):
    # A stray right-button release while the left button is still dragging must
    # not finish the annotation early — the drag continues and only the left
    # release commits it (regression: mouseReleaseEvent ignored the button).
    canvas = _canvas(qtbot)
    canvas.set_tool(Tool.RECT)
    viewport = canvas.viewport()
    qtbot.mousePress(viewport, Qt.LeftButton, pos=QPoint(15, 15))
    qtbot.mouseMove(viewport, pos=QPoint(40, 35))
    qtbot.mouseRelease(viewport, Qt.RightButton, pos=QPoint(40, 35))
    assert canvas.undo_stack().count() == 0  # nothing committed yet

    qtbot.mouseMove(viewport, pos=QPoint(80, 60))
    qtbot.mouseRelease(viewport, Qt.LeftButton, pos=QPoint(80, 60))
    assert canvas.undo_stack().count() == 1
    rects = [i for i in canvas.scene().items() if hasattr(i, "rect") and i.zValue() > -1000]
    assert len(rects) == 1
    assert rects[0].rect().width() > 40  # the drag kept going past the right-release point


def _draw_rect(qtbot, canvas):
    canvas.set_tool(Tool.RECT)
    viewport = canvas.viewport()
    qtbot.mousePress(viewport, Qt.LeftButton, pos=QPoint(20, 20))
    qtbot.mouseMove(viewport, pos=QPoint(80, 60))
    qtbot.mouseRelease(viewport, Qt.LeftButton, pos=QPoint(80, 60))
    return next(i for i in canvas.scene().items() if i.zValue() > -1000)


def _annotation_items(canvas):
    return [item for item in canvas.scene().items() if item is not canvas._background]


def test_move_items_command_round_trips_position(qtbot):
    from PySide6.QtCore import QPointF

    from shotquill.ui.canvas import _MoveItemsCommand

    canvas = _canvas(qtbot)
    item = _draw_rect(qtbot, canvas)
    old = item.pos()
    new = old + QPointF(10, 12)
    stack = canvas.undo_stack()

    stack.push(_MoveItemsCommand([(item, old, new)]))
    assert item.pos() == new
    assert stack.count() == 2  # the add, then the move
    stack.undo()
    assert item.pos() == old
    stack.redo()
    assert item.pos() == new


def test_dragging_a_selected_item_is_undoable(qtbot):
    canvas = _canvas(qtbot)
    item = _draw_rect(qtbot, canvas)
    assert canvas.undo_stack().count() == 1
    start = item.pos()

    canvas.set_tool(Tool.SELECT)
    item.setSelected(True)
    viewport = canvas.viewport()
    qtbot.mousePress(viewport, Qt.LeftButton, pos=QPoint(45, 40))
    qtbot.mouseMove(viewport, pos=QPoint(58, 52))
    qtbot.mouseMove(viewport, pos=QPoint(70, 64))
    qtbot.mouseRelease(viewport, Qt.LeftButton, pos=QPoint(70, 64))

    assert item.pos() != start  # the drag actually moved it
    assert canvas.undo_stack().count() == 2  # the move is recorded
    canvas.undo_stack().undo()
    assert item.pos() == start  # undo restores the original position


def test_select_click_without_moving_records_no_undo(qtbot):
    canvas = _canvas(qtbot)
    item = _draw_rect(qtbot, canvas)
    assert canvas.undo_stack().count() == 1

    canvas.set_tool(Tool.SELECT)
    item.setSelected(True)
    viewport = canvas.viewport()
    # A plain click (press + release at the same spot) selects but moves nothing.
    qtbot.mousePress(viewport, Qt.LeftButton, pos=QPoint(45, 40))
    qtbot.mouseRelease(viewport, Qt.LeftButton, pos=QPoint(45, 40))
    assert canvas.undo_stack().count() == 1  # no spurious move command


@pytest.mark.parametrize("key", [Qt.Key_Backspace, Qt.Key_Delete])
def test_delete_keys_remove_selected_annotation_and_are_undoable(qtbot, key):
    canvas = _canvas(qtbot)
    item = _draw_rect(qtbot, canvas)
    item.setSelected(True)

    qtbot.keyClick(canvas, key)

    assert item.scene() is None
    assert _annotation_items(canvas) == []
    assert canvas.undo_stack().count() == 2

    canvas.undo_stack().undo()
    assert item.scene() is canvas.scene()
    assert item.isSelected()

    canvas.undo_stack().redo()
    assert item.scene() is None
    assert _annotation_items(canvas) == []


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


def test_select_tool_uses_the_default_cursor(qtbot):
    canvas = _canvas(qtbot)
    canvas.set_tool(Tool.RECT)
    canvas.set_tool(Tool.SELECT)
    assert canvas.viewport().cursor().shape() == Qt.ArrowCursor


def test_drawing_tools_use_a_distinct_cursor(qtbot):
    canvas = _canvas(qtbot)
    canvas.set_tool(Tool.RECT)
    assert canvas.viewport().cursor().shape() == Qt.CrossCursor


def test_text_tool_uses_text_cursor(qtbot):
    canvas = _canvas(qtbot)
    canvas.set_tool(Tool.TEXT)
    assert canvas.viewport().cursor().shape() == Qt.IBeamCursor


def test_idle_cursor_keeps_existing_tool_cursor(qtbot):
    canvas = _canvas(qtbot)
    viewport = canvas.viewport()

    canvas.set_tool(Tool.RECT)
    canvas._update_idle_cursor()

    assert viewport.testAttribute(Qt.WA_SetCursor)
    assert viewport.cursor().shape() == Qt.CrossCursor


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


def _text_items(canvas):
    from PySide6.QtWidgets import QGraphicsTextItem

    return [item for item in canvas._scene.items() if isinstance(item, QGraphicsTextItem)]


def _click_text_tool(qtbot, canvas):
    canvas.set_tool(Tool.TEXT)
    viewport = canvas.viewport()
    qtbot.mousePress(viewport, Qt.LeftButton, pos=QPoint(30, 30))
    qtbot.mouseRelease(viewport, Qt.LeftButton, pos=QPoint(30, 30))


def _finish_editing(item):
    """Deliver the focus-out that ends text editing.

    Offscreen the scene is never active, so items never truly gain focus and
    ``clearFocus`` won't emit the event; dispatch it directly to exercise the
    ``_TextItem.focusOutEvent`` override the real desktop relies on.
    """
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QFocusEvent

    item.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))


def test_text_tool_creates_item_on_single_click(qtbot):
    canvas = _canvas(qtbot)
    _click_text_tool(qtbot, canvas)
    assert len(_text_items(canvas)) == 1
    # The undo entry is deferred until editing finishes (focus-out): an item
    # that may yet be discarded as empty must not be undoable.
    assert canvas.undo_stack().count() == 0


def test_empty_text_item_is_discarded_on_focus_out(qtbot):
    # A stray click with the text tool must not leave an invisible, selectable,
    # undoable item behind.
    canvas = _canvas(qtbot)
    _click_text_tool(qtbot, canvas)
    _finish_editing(_text_items(canvas)[0])
    assert _text_items(canvas) == []
    assert canvas.undo_stack().count() == 0


def test_starting_a_text_edit_freezes_crop_even_if_discarded(qtbot):
    # Starting a text annotation latches the crop closed; discarding the empty
    # item must not re-open crop adjustment (which would let the shot shift
    # under a half-finished annotation).
    canvas = _canvas(qtbot)
    assert canvas.is_pristine()
    _click_text_tool(qtbot, canvas)
    assert not canvas.is_pristine()
    _finish_editing(_text_items(canvas)[0])  # discarded as empty
    assert _text_items(canvas) == []
    assert canvas.undo_stack().count() == 0
    assert not canvas.is_pristine()  # stays frozen


def test_text_item_with_content_becomes_undoable_on_focus_out(qtbot):
    canvas = _canvas(qtbot)
    _click_text_tool(qtbot, canvas)
    item = _text_items(canvas)[0]
    item.setPlainText("note")
    _finish_editing(item)
    assert len(_text_items(canvas)) == 1
    assert canvas.undo_stack().count() == 1
    canvas.undo_stack().undo()
    assert _text_items(canvas) == []


def test_backspace_deletes_selected_text_annotation_after_editing_finishes(qtbot):
    canvas = _canvas(qtbot)
    _click_text_tool(qtbot, canvas)
    item = _text_items(canvas)[0]
    item.setPlainText("note")
    _finish_editing(item)
    canvas._scene.clearFocus()
    item.setSelected(True)

    qtbot.keyClick(canvas, Qt.Key_Backspace)

    assert _text_items(canvas) == []
    assert canvas.undo_stack().count() == 2
    canvas.undo_stack().undo()
    assert _text_items(canvas) == [item]


def test_backspace_does_not_delete_text_annotation_while_editing(qtbot):
    canvas = _canvas(qtbot)
    _click_text_tool(qtbot, canvas)
    item = _text_items(canvas)[0]
    item.setPlainText("note")
    item.setSelected(True)
    canvas._scene.setFocusItem(item, Qt.OtherFocusReason)

    qtbot.keyClick(canvas, Qt.Key_Backspace)

    assert item.scene() is canvas.scene()
    assert _text_items(canvas) == [item]
    assert canvas.undo_stack().count() == 0


def test_whitespace_only_text_item_is_discarded_on_focus_out(qtbot):
    canvas = _canvas(qtbot)
    _click_text_tool(qtbot, canvas)
    item = _text_items(canvas)[0]
    item.setPlainText("   ")
    _finish_editing(item)
    assert _text_items(canvas) == []
    assert canvas.undo_stack().count() == 0


def test_repeated_focus_out_after_commit_is_idempotent(qtbot):
    # Qt can deliver more focus-out events later (e.g. when the scene clears
    # focus during removal); they must not double-push or discard the item.
    canvas = _canvas(qtbot)
    _click_text_tool(qtbot, canvas)
    item = _text_items(canvas)[0]
    item.setPlainText("note")
    _finish_editing(item)
    _finish_editing(item)
    assert len(_text_items(canvas)) == 1
    assert canvas.undo_stack().count() == 1


def test_mosaic_drag_throttles_live_updates_but_release_renders_final_rect(qtbot, monkeypatch):
    # Live pixelation is capped (expensive on big shots); the release must
    # still render the exact final rect even if the last moves were skipped.
    import types

    from shotquill.ui import canvas as canvas_module
    from shotquill.ui.items.mosaic import MosaicItem

    clock = {"now": 1000.0}
    monkeypatch.setattr(
        canvas_module, "time", types.SimpleNamespace(monotonic=lambda: clock["now"])
    )
    updates = []
    real_update = MosaicItem.update_rect

    def _tracking_update(self, rect):
        updates.append(rect)
        real_update(self, rect)

    monkeypatch.setattr(MosaicItem, "update_rect", _tracking_update)

    canvas = _canvas(qtbot)
    canvas.set_tool(Tool.MOSAIC)
    viewport = canvas.viewport()
    qtbot.mousePress(viewport, Qt.LeftButton, pos=QPoint(10, 10))
    qtbot.mouseMove(viewport, pos=QPoint(30, 30))  # first move renders
    assert len(updates) == 1
    clock["now"] += 0.001  # within the throttle window
    qtbot.mouseMove(viewport, pos=QPoint(50, 40))  # skipped by the throttle
    qtbot.mouseMove(viewport, pos=QPoint(70, 50))  # skipped by the throttle
    assert len(updates) == 1
    qtbot.mouseRelease(viewport, Qt.LeftButton, pos=QPoint(70, 50))
    # Release renders the latest drag rect exactly.
    assert len(updates) == 2
    assert updates[-1].width() > updates[0].width()
    assert canvas.undo_stack().count() == 1
