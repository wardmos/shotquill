# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""The annotation canvas: a QGraphicsView that draws shapes on a screenshot.

Tool-driven mouse handling creates items live during a drag; each finished item
is pushed onto a QUndoStack so undo/redo works. ``export_image`` renders the
screenshot plus annotations back to a QImage for copy/save.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QUndoCommand,
    QUndoStack,
)
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
)

from shotquill.ui._debug import crop_log
from shotquill.ui.geometry import crop_edge_hits
from shotquill.ui.items.arrow import ArrowItem
from shotquill.ui.items.mosaic import MosaicItem
from shotquill.ui.tools import Tool

if TYPE_CHECKING:
    from collections.abc import Callable

    from shotquill.ui.editor import CropHost

_DEFAULT_COLOR = "#ff3b30"
_NEGLIGIBLE = 3.0
# Keys the editor window uses to adjust the crop region; the canvas must not
# swallow them (QGraphicsView would scroll, uselessly — scrollbars are off).
_CROP_ADJUST_KEYS = (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down)
# How near a viewport edge (logical points) a hover/press counts as grabbing
# that edge of the crop, to enter mouse crop-adjustment (region captures, while
# the canvas is still pristine).
_CROP_EDGE_MARGIN = 10.0
# Pixelating the whole selection on every mouse move is expensive on big
# (Retina) shots; cap live mosaic regeneration to roughly this rate. The
# release handler always renders the final rect, so no precision is lost.
_MOSAIC_PREVIEW_INTERVAL = 1 / 30  # seconds
_FREEHAND_MIN_VIEW_DELTA = 0.75
_SELECTION_OUTLINE_COLOR = "#2d7ff9"
_SELECTION_OUTLINE_VIEW_PADDING = 4.0
_SELECTION_HANDLE_VIEW_SIZE = 8.0
_SELECTION_HIT_VIEW_TOLERANCE = 8.0
_SELECTION_CLICK_VIEW_TOLERANCE = 4


class _TextItem(QGraphicsTextItem):
    """A text annotation that defers its undo entry until editing finishes.

    Created empty and focused for typing; when focus leaves, the canvas decides
    its fate — discard if still empty (a stray click must not leave an
    invisible, undoable item behind), otherwise push it onto the undo stack.
    ``committed`` flips once that decision is made so re-entrant focus-out
    events (e.g. from the removal itself) do nothing.
    """

    def __init__(self, on_editing_finished: Callable[[_TextItem], None]) -> None:
        super().__init__()
        self.committed = False
        self._on_editing_finished = on_editing_finished

    def focusOutEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().focusOutEvent(event)
        self._on_editing_finished(self)


class _AddItemCommand(QUndoCommand):
    """Undoable insertion of an annotation item (it is already on the scene)."""

    def __init__(self, scene: QGraphicsScene, item: QGraphicsItem) -> None:
        super().__init__("add annotation")
        self._scene = scene
        self._item = item
        self._on_scene = True

    def undo(self) -> None:
        self._scene.removeItem(self._item)
        self._on_scene = False

    def redo(self) -> None:
        if not self._on_scene:
            self._scene.addItem(self._item)
            self._on_scene = True


class _MoveItemsCommand(QUndoCommand):
    """Undoable move of annotation items (already dragged to their new spots).

    ``ItemIsMovable`` lets the select tool drag items, but Qt mutates ``pos()``
    directly with no undo entry — so without this an undo after a move restores
    nothing, and an undo of a *later* edit leaves the move silently applied. We
    snapshot positions on press and record this command on release for whatever
    actually moved.
    """

    def __init__(self, moves: list[tuple[QGraphicsItem, QPointF, QPointF]]) -> None:
        super().__init__("move annotation")
        self._moves = moves

    def undo(self) -> None:
        for item, old, _new in self._moves:
            item.setPos(old)

    def redo(self) -> None:
        for item, _old, new in self._moves:
            item.setPos(new)


class _DeleteItemsCommand(QUndoCommand):
    """Undoable removal of selected annotation items."""

    def __init__(self, scene: QGraphicsScene, items: list[QGraphicsItem]) -> None:
        super().__init__("delete annotation")
        self._scene = scene
        self._items = [(item, item.isSelected()) for item in items]

    def undo(self) -> None:
        for item, was_selected in self._items:
            if item.scene() is None:
                self._scene.addItem(item)
            item.setSelected(was_selected)

    def redo(self) -> None:
        for item, _was_selected in self._items:
            if item.scene() is self._scene:
                item.setSelected(False)
                self._scene.removeItem(item)


class AnnotationCanvas(QGraphicsView):
    def __init__(self, background: QPixmap) -> None:
        super().__init__()
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._background_pixmap = background
        self._background = self._scene.addPixmap(background)
        self._background.setZValue(-1000)
        self._scene.setSceneRect(QRectF(background.rect()))

        self.setRenderHint(QPainter.Antialiasing)
        self.setMouseTracking(True)

        self._undo = QUndoStack(self)
        self._tool = Tool.SELECT
        self._color = QColor(_DEFAULT_COLOR)
        self._width = 9
        self._z = 0.0
        self._temp_item: QGraphicsItem | None = None
        self._last_hit_item: QGraphicsItem | None = None
        self._press_hit_item: QGraphicsItem | None = None
        self._press_view_pos = None
        # Positions of movable items captured at the start of a select-drag, so
        # the release can record an undoable move for whatever actually shifted.
        self._move_snapshot: dict[QGraphicsItem, QPointF] | None = None
        self._path: QPainterPath | None = None
        self._last_path_pos: QPointF | None = None
        self._start = QPointF()
        self._mosaic_rect = None  # latest drag rect; release renders it exactly
        self._mosaic_last = 0.0  # monotonic time of the last live mosaic render
        # One-way latch: starting a text edit commits to annotating even if the
        # text is later discarded as empty, so the crop can't become adjustable
        # again under a half-finished annotation. See ``is_pristine``.
        self._text_started = False
        self._closing = False  # set on teardown; stops late focus-out commits
        # Crop edge-adjust (region captures only): the editor registers itself
        # as the host; a press on a viewport edge hands off to it (see the mouse
        # handlers and set_crop_host).
        self._crop_host: CropHost | None = None
        self._scene.selectionChanged.connect(self._update_selection_effects)
        self._apply_drag_mode()

    # --- public API used by the toolbar / window --------------------------

    def undo_stack(self) -> QUndoStack:
        return self._undo

    def background_image(self) -> QImage:
        """The original (un-annotated) screenshot, for OCR."""
        return self._background_pixmap.toImage()

    def set_crop_host(self, host: CropHost) -> None:
        """Let the editor drive mouse crop-adjustment from canvas edge gestures.

        While the host reports the crop is still adjustable, hovering a viewport
        edge shows a resize cursor and pressing it hands off to the host, which
        opens the full-screen adjust surface (see the mouse handlers).
        """
        self._crop_host = host

    def set_background(self, background: QPixmap) -> None:
        """Swap the screenshot under the (empty) scene — crop adjustment.

        Only called while the canvas is pristine (see ``is_pristine``), so no
        annotation can be left misaligned over the re-cropped pixels.
        """
        self._background_pixmap = background
        self._background.setPixmap(background)
        self._scene.setSceneRect(QRectF(background.rect()))

    def is_pristine(self) -> bool:
        """True while nothing has been annotated: no undo history, no text edit
        ever started, and nothing on the scene beyond the background screenshot
        (an uncommitted text item counts as an annotation)."""
        return not self._text_started and self._undo.count() == 0 and len(self._scene.items()) == 1

    def color(self) -> QColor:
        return QColor(self._color)

    def width(self) -> int:
        return self._width

    def set_tool(self, tool: Tool) -> None:
        self._tool = tool
        self._apply_drag_mode()
        self._update_idle_cursor()

    def set_color(self, color: QColor) -> None:
        self._color = QColor(color)

    def set_width(self, width: int) -> None:
        self._width = max(1, int(width))

    def export_image(self) -> QImage:
        self._scene.clearSelection()
        source = self._background.boundingRect()
        image = QImage(int(source.width()), int(source.height()), QImage.Format.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        self._scene.render(painter, QRectF(image.rect()), source)
        painter.end()
        return image

    # --- internals --------------------------------------------------------

    def _apply_drag_mode(self) -> None:
        if self._tool == Tool.SELECT:
            self.setDragMode(QGraphicsView.RubberBandDrag)
        else:
            self.setDragMode(QGraphicsView.NoDrag)

    def _crop_edges_at(self, pos) -> tuple[bool, bool, bool, bool]:
        """Which crop edges the pointer (viewport coords) grabs, or all-False.

        Only the SELECT tool adjusts the crop (the drawing tools own the drag),
        and only while the host reports the crop is still adjustable.
        """
        if self._crop_host is None or self._tool != Tool.SELECT:
            return (False, False, False, False)
        if not self._crop_host.crop_adjustable():
            return (False, False, False, False)
        viewport = self.viewport()
        return crop_edge_hits(
            pos.x(), pos.y(), viewport.width(), viewport.height(), _CROP_EDGE_MARGIN
        )

    @staticmethod
    def _crop_cursor(edges: tuple[bool, bool, bool, bool]):
        """The resize cursor for the grabbed ``edges``, or None when none are."""
        left, top, right, bottom = edges
        if (left and top) or (right and bottom):
            return Qt.SizeFDiagCursor
        if (right and top) or (left and bottom):
            return Qt.SizeBDiagCursor
        if left or right:
            return Qt.SizeHorCursor
        if top or bottom:
            return Qt.SizeVerCursor
        return None

    def _set_viewport_cursor(self, shape) -> None:
        viewport = self.viewport()
        if shape is None:
            if viewport.testAttribute(Qt.WA_SetCursor):
                viewport.unsetCursor()
            return
        if not viewport.testAttribute(Qt.WA_SetCursor) or viewport.cursor().shape() != shape:
            viewport.setCursor(shape)

    def _update_crop_cursor(self, pos) -> None:
        self._set_viewport_cursor(self._crop_cursor(self._crop_edges_at(pos)))

    def _update_idle_cursor(self, pos=None) -> None:
        if self._tool == Tool.SELECT:
            if pos is None:
                self._set_viewport_cursor(None)
            else:
                self._update_crop_cursor(pos)
            return
        if self._tool == Tool.TEXT:
            self._set_viewport_cursor(Qt.IBeamCursor)
        else:
            self._set_viewport_cursor(Qt.CrossCursor)

    def delete_selected_items(self) -> bool:
        selected = self._selected_annotation_items()
        if not selected:
            fallback = self._delete_fallback_item()
            if fallback is not None:
                selected = [fallback]
        if not selected:
            return False
        self._undo.push(_DeleteItemsCommand(self._scene, selected))
        self._last_hit_item = None
        return True

    def _annotation_items(self) -> list[QGraphicsItem]:
        return [
            item
            for item in self._scene.items()
            if item is not self._background and item.scene() is self._scene
        ]

    def _selected_annotation_items(self) -> list[QGraphicsItem]:
        return [
            item
            for item in self._scene.selectedItems()
            if item is not self._background and item.scene() is self._scene
        ]

    def _delete_fallback_item(self) -> QGraphicsItem | None:
        candidates = (self._scene.focusItem(), self._last_hit_item, self._item_under_cursor())
        for item in candidates:
            if item in self._annotation_items() and item.flags() & QGraphicsItem.ItemIsSelectable:
                return item
        annotations = self._annotation_items()
        if len(annotations) == 1 and annotations[0].flags() & QGraphicsItem.ItemIsSelectable:
            return annotations[0]
        return None

    def _item_under_cursor(self) -> QGraphicsItem | None:
        pos = self.viewport().mapFromGlobal(QCursor.pos())
        if not self.viewport().rect().contains(pos):
            return None
        item = self.itemAt(pos)
        return item if item is not self._background else None

    def _annotation_item_at_view_pos(self, pos) -> QGraphicsItem | None:
        scene_pos = self.mapToScene(pos)
        padding = self._scene_units_for_view_pixels(_SELECTION_HIT_VIEW_TOLERANCE)
        for item in sorted(self._annotation_items(), key=lambda it: it.zValue(), reverse=True):
            hit_rect = item.mapRectToScene(item.boundingRect()).adjusted(
                -padding, -padding, padding, padding
            )
            if hit_rect.contains(scene_pos):
                return item
        return None

    def _select_annotation_item(self, item: QGraphicsItem | None) -> bool:
        if item is None or item.scene() is not self._scene:
            return False
        if not item.flags() & QGraphicsItem.ItemIsSelectable:
            return False
        self._scene.clearSelection()
        item.setSelected(True)
        self._last_hit_item = item
        return True

    def _is_click_release(self, pos) -> bool:
        return (
            self._press_view_pos is not None
            and (pos - self._press_view_pos).manhattanLength() <= _SELECTION_CLICK_VIEW_TOLERANCE
        )

    def _update_selection_effects(self) -> None:
        self.viewport().update()

    def _scene_units_for_view_pixels(self, pixels: float) -> float:
        transform = self.transform()
        scale = max(abs(transform.m11()), abs(transform.m22()), 0.01)
        return pixels / scale

    def _selected_annotation_scene_rects(self) -> list[QRectF]:
        padding = self._scene_units_for_view_pixels(_SELECTION_OUTLINE_VIEW_PADDING)
        return [
            item.mapRectToScene(item.boundingRect()).adjusted(-padding, -padding, padding, padding)
            for item in self._selected_annotation_items()
        ]

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:  # noqa: N802 (Qt override)
        super().drawForeground(painter, rect)
        selected_rects = self._selected_annotation_scene_rects()
        if not selected_rects:
            return

        pen = QPen(QColor(_SELECTION_OUTLINE_COLOR))
        pen.setWidthF(1.5)
        pen.setCosmetic(True)
        pen.setStyle(Qt.DashLine)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        handle = self._scene_units_for_view_pixels(_SELECTION_HANDLE_VIEW_SIZE)
        for selected_rect in selected_rects:
            visible = selected_rect.intersected(rect)
            if visible.isNull():
                continue
            painter.drawRect(selected_rect)
            left = selected_rect.left()
            right = selected_rect.right()
            top = selected_rect.top()
            bottom = selected_rect.bottom()
            painter.drawLine(QPointF(left, top), QPointF(left + handle, top))
            painter.drawLine(QPointF(left, top), QPointF(left, top + handle))
            painter.drawLine(QPointF(right, top), QPointF(right - handle, top))
            painter.drawLine(QPointF(right, top), QPointF(right, top + handle))
            painter.drawLine(QPointF(left, bottom), QPointF(left + handle, bottom))
            painter.drawLine(QPointF(left, bottom), QPointF(left, bottom - handle))
            painter.drawLine(QPointF(right, bottom), QPointF(right - handle, bottom))
            painter.drawLine(QPointF(right, bottom), QPointF(right, bottom - handle))
        painter.restore()

    def _next_z(self) -> float:
        self._z += 1.0
        return self._z

    def _pen(self, *, highlighter: bool = False) -> QPen:
        color = QColor(self._color)
        width = self._width
        if highlighter:
            color.setAlpha(110)
            width = max(self._width * 4, 12)
        pen = QPen(color, width)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        return pen

    def keyPressEvent(self, event) -> None:
        if (
            event.key() in (Qt.Key_Backspace, Qt.Key_Delete)
            and self._scene.focusItem() is None
            and self.delete_selected_items()
        ):
            event.accept()
            return
        # Arrow keys belong to the window's crop adjustment while no text
        # annotation has focus (a focused text item still gets them for cursor
        # movement via the scene). Without this, QAbstractScrollArea would
        # accept them for scrolling and the window would never see them.
        if event.key() in _CROP_ADJUST_KEYS and self._scene.focusItem() is None:
            event.ignore()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._press_view_pos = event.position().toPoint()
            self._press_hit_item = self._annotation_item_at_view_pos(self._press_view_pos)
        else:
            self._press_view_pos = None
            self._press_hit_item = None
        # A press on a crop edge (region capture, still pristine) opens the
        # full-screen adjust surface instead of starting a rubber-band select.
        if event.button() == Qt.LeftButton:
            edges = self._crop_edges_at(event.position())
            crop_log(
                f"canvas.press pos={event.position().toPoint()} "
                f"vp=({self.viewport().width()},{self.viewport().height()}) "
                f"host={self._crop_host is not None} tool={self._tool} edges={edges}"
            )
            if any(edges):
                self._crop_host.enter_crop_adjust(edges)
                return

        if event.button() != Qt.LeftButton or self._tool == Tool.SELECT:
            if event.button() == Qt.LeftButton and self._tool == Tool.SELECT:
                self._last_hit_item = self.itemAt(event.position().toPoint())
                if self._last_hit_item is self._background:
                    self._last_hit_item = None
                # Snapshot movable items before Qt drags them, so the matching
                # release can push an undoable move for any that shift.
                self._move_snapshot = {
                    it: it.pos()
                    for it in self._scene.items()
                    if it.flags() & QGraphicsItem.ItemIsMovable
                }
            super().mousePressEvent(event)
            return

        self._start = self.mapToScene(event.position().toPoint())
        tool = self._tool

        if tool == Tool.TEXT:
            self._create_text(self._start)
            return

        item: QGraphicsItem | None = None
        if tool in (Tool.PEN, Tool.HIGHLIGHTER):
            self._path = QPainterPath(self._start)
            self._last_path_pos = QPointF(self._start)
            path_item = QGraphicsPathItem(self._path)
            path_item.setPen(self._pen(highlighter=tool == Tool.HIGHLIGHTER))
            item = path_item
        elif tool == Tool.RECT:
            rect_item = QGraphicsRectItem(QRectF(self._start, self._start))
            rect_item.setPen(self._pen())
            item = rect_item
        elif tool == Tool.ELLIPSE:
            ellipse_item = QGraphicsEllipseItem(QRectF(self._start, self._start))
            ellipse_item.setPen(self._pen())
            item = ellipse_item
        elif tool == Tool.LINE:
            line_item = QGraphicsLineItem(QLineF(self._start, self._start))
            line_item.setPen(self._pen())
            item = line_item
        elif tool == Tool.ARROW:
            arrow_item = ArrowItem(QLineF(self._start, self._start))
            arrow_item.setPen(self._pen())
            item = arrow_item
        elif tool == Tool.MOSAIC:
            item = MosaicItem(self._background_pixmap)
            self._mosaic_rect = None
            self._mosaic_last = 0.0  # first move renders immediately

        if item is not None:
            item.setZValue(self._next_z())
            self._scene.addItem(item)
            self._temp_item = item

    def mouseMoveEvent(self, event) -> None:
        if self._temp_item is None:
            # Idle hover: show a resize cursor over a crop edge so the adjust
            # gesture is discoverable. True hover only — a held button is a
            # rubber-band select and must not flip the cursor.
            if not event.buttons():
                self._update_idle_cursor(event.position())
            super().mouseMoveEvent(event)
            return

        pos = self.mapToScene(event.position().toPoint())
        tool = self._tool
        if tool in (Tool.PEN, Tool.HIGHLIGHTER) and self._path is not None:
            if not self._freehand_point_moved_enough(pos):
                return
            self._path.lineTo(pos)
            self._last_path_pos = QPointF(pos)
            self._temp_item.setPath(self._path)
        elif tool in (Tool.RECT, Tool.ELLIPSE):
            self._temp_item.setRect(QRectF(self._start, pos).normalized())
        elif tool in (Tool.LINE, Tool.ARROW):
            self._temp_item.setLine(QLineF(self._start, pos))
        elif tool == Tool.MOSAIC:
            self._mosaic_rect = QRectF(self._start, pos).normalized().toRect()
            now = time.monotonic()
            if now - self._mosaic_last >= _MOSAIC_PREVIEW_INTERVAL:
                self._mosaic_last = now
                self._temp_item.update_rect(self._mosaic_rect)

    def mouseReleaseEvent(self, event) -> None:
        # A select-drag finishing: let Qt commit the new positions, then record
        # an undoable move for whatever actually shifted (a plain click or a
        # rubber-band select moves nothing and pushes no command).
        if self._move_snapshot is not None and event.button() == Qt.LeftButton:
            super().mouseReleaseEvent(event)
            moved = [
                (it, old, it.pos())
                for it, old in self._move_snapshot.items()
                if it.scene() is self._scene and it.pos() != old
            ]
            self._move_snapshot = None
            if moved:
                self._undo.push(_MoveItemsCommand(moved))
            elif self._is_click_release(event.position().toPoint()):
                self._select_annotation_item(self._press_hit_item)
            return

        # Only the left button finishes a drag: a stray right/middle release
        # mid-drag must not commit the half-drawn item (the press handler only
        # ever starts items on the left button).
        if event.button() != Qt.LeftButton or self._temp_item is None:
            super().mouseReleaseEvent(event)
            return

        item = self._temp_item
        self._temp_item = None
        self._path = None
        self._last_path_pos = None

        if isinstance(item, MosaicItem) and self._mosaic_rect is not None:
            # The live preview is throttled; render the final drag rect exactly.
            item.update_rect(self._mosaic_rect)
            self._mosaic_rect = None

        if self._is_negligible(item):
            self._scene.removeItem(item)
            if self._is_click_release(event.position().toPoint()):
                self._select_annotation_item(self._press_hit_item)
            return

        item.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable)
        self._undo.push(_AddItemCommand(self._scene, item))

    def _create_text(self, pos: QPointF) -> None:
        # The undo entry is deferred to _finish_text: only text that survives
        # its first focus-out (i.e. is non-empty) becomes part of the document.
        self._text_started = True  # latch: never re-enable crop adjustment
        item = _TextItem(self._finish_text)
        item.setDefaultTextColor(self._color)
        font = QFont()
        font.setPointSize(max(self._width * 4, 16))
        item.setFont(font)
        item.setPos(pos)
        item.setTextInteractionFlags(Qt.TextEditorInteraction)
        item.setFlags(
            QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsFocusable
        )
        item.setZValue(self._next_z())
        self._scene.addItem(item)
        item.setFocus()

    def _freehand_point_moved_enough(self, pos: QPointF) -> bool:
        if self._last_path_pos is None:
            return True
        min_delta = self._scene_units_for_view_pixels(_FREEHAND_MIN_VIEW_DELTA)
        dx = pos.x() - self._last_path_pos.x()
        dy = pos.y() - self._last_path_pos.y()
        return dx * dx + dy * dy >= min_delta * min_delta

    def begin_teardown(self) -> None:
        """Stop committing text on focus-out — the window is closing.

        ``WA_DeleteOnClose`` fires a focus-out on the active text item while the
        view/scene are being destroyed; pushing onto the (dying) undo stack then
        risks a ``RuntimeError``. The on-screen pixels are already captured, so
        the deferred commit is moot at this point."""
        self._closing = True

    def _finish_text(self, item: _TextItem) -> None:
        """First focus-out commits a text item: empty → discarded, else undoable."""
        if item.committed or self._closing:
            return
        item.committed = True
        if not item.toPlainText().strip():
            self._scene.removeItem(item)
            return
        self._undo.push(_AddItemCommand(self._scene, item))

    @staticmethod
    def _is_negligible(item: QGraphicsItem) -> bool:
        if isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem)):
            rect = item.rect()
            return rect.width() < _NEGLIGIBLE and rect.height() < _NEGLIGIBLE
        if isinstance(item, QGraphicsLineItem):  # also covers ArrowItem
            return item.line().length() < _NEGLIGIBLE
        if isinstance(item, MosaicItem):
            # Explicit validity: a drag that ended outside the background has
            # no region even if an earlier pixmap once gave it a bounding rect.
            return not item.has_region()
        if isinstance(item, (QGraphicsPathItem, QGraphicsPixmapItem)):
            rect = item.boundingRect()
            return rect.width() < _NEGLIGIBLE and rect.height() < _NEGLIGIBLE
        return False
