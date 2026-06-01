# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""The annotation canvas: a QGraphicsView that draws shapes on a screenshot.

Tool-driven mouse handling creates items live during a drag; each finished item
is pushed onto a QUndoStack so undo/redo works. ``export_image`` renders the
screenshot plus annotations back to a QImage for copy/save.
"""

from __future__ import annotations

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
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
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
)

from shotquill.ui.items.arrow import ArrowItem
from shotquill.ui.tools import Tool

_DEFAULT_COLOR = "#ff3b30"
_NEGLIGIBLE = 3.0


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


class AnnotationCanvas(QGraphicsView):
    def __init__(self, background: QPixmap) -> None:
        super().__init__()
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._background = self._scene.addPixmap(background)
        self._background.setZValue(-1000)
        self._scene.setSceneRect(QRectF(background.rect()))

        self.setRenderHint(QPainter.Antialiasing)
        self.setMouseTracking(True)

        self._undo = QUndoStack(self)
        self._tool = Tool.SELECT
        self._color = QColor(_DEFAULT_COLOR)
        self._width = 4
        self._z = 0.0
        self._temp_item: QGraphicsItem | None = None
        self._path: QPainterPath | None = None
        self._start = QPointF()
        self._apply_drag_mode()

    # --- public API used by the toolbar / window --------------------------

    def undo_stack(self) -> QUndoStack:
        return self._undo

    def color(self) -> QColor:
        return QColor(self._color)

    def width(self) -> int:
        return self._width

    def set_tool(self, tool: Tool) -> None:
        self._tool = tool
        self._apply_drag_mode()

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

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton or self._tool == Tool.SELECT:
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

        if item is not None:
            item.setZValue(self._next_z())
            self._scene.addItem(item)
            self._temp_item = item

    def mouseMoveEvent(self, event) -> None:
        if self._temp_item is None:
            super().mouseMoveEvent(event)
            return

        pos = self.mapToScene(event.position().toPoint())
        tool = self._tool
        if tool in (Tool.PEN, Tool.HIGHLIGHTER) and self._path is not None:
            self._path.lineTo(pos)
            self._temp_item.setPath(self._path)
        elif tool in (Tool.RECT, Tool.ELLIPSE):
            self._temp_item.setRect(QRectF(self._start, pos).normalized())
        elif tool in (Tool.LINE, Tool.ARROW):
            self._temp_item.setLine(QLineF(self._start, pos))

    def mouseReleaseEvent(self, event) -> None:
        if self._temp_item is None:
            super().mouseReleaseEvent(event)
            return

        item = self._temp_item
        self._temp_item = None
        self._path = None

        if self._is_negligible(item):
            self._scene.removeItem(item)
            return

        item.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable)
        self._undo.push(_AddItemCommand(self._scene, item))

    def _create_text(self, pos: QPointF) -> None:
        item = QGraphicsTextItem()
        item.setDefaultTextColor(self._color)
        font = QFont()
        font.setPointSize(max(self._width * 4, 16))
        item.setFont(font)
        item.setPos(pos)
        item.setTextInteractionFlags(Qt.TextEditorInteraction)
        item.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable)
        item.setZValue(self._next_z())
        self._scene.addItem(item)
        self._undo.push(_AddItemCommand(self._scene, item))
        item.setFocus()

    @staticmethod
    def _is_negligible(item: QGraphicsItem) -> bool:
        if isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem)):
            rect = item.rect()
            return rect.width() < _NEGLIGIBLE and rect.height() < _NEGLIGIBLE
        if isinstance(item, QGraphicsLineItem):  # also covers ArrowItem
            return item.line().length() < _NEGLIGIBLE
        if isinstance(item, QGraphicsPathItem):
            rect = item.boundingRect()
            return rect.width() < _NEGLIGIBLE and rect.height() < _NEGLIGIBLE
        return False
