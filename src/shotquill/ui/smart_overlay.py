# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Full-screen smart-capture overlay.

Shows a frozen, dimmed screenshot of the whole virtual desktop and picks the
capture mode from what the pointer does — no separate region/window hotkeys:

* Hovering an application window lights it up; a click captures that window.
* Hovering empty space lights the whole desktop; a click captures full screen.
* Pressing and dragging draws a rectangle and captures that region on release.

Esc or a right-click cancels. This folds the old ``RegionOverlay`` and
``WindowPicker`` into one interaction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from shotquill.i18n import t
from shotquill.ui.geometry import scale_rect, selection_rect, window_at_point

if TYPE_CHECKING:
    from shotquill.capture.base import WindowInfo

_DIM = QColor(0, 0, 0, 120)
_ACCENT = QColor("#2d7ff9")
_MIN_SIZE = 2
# How far the pointer must travel after a press before we treat it as a region
# drag rather than a click on the hovered window / full screen.
_DRAG_THRESHOLD = 4


class SmartOverlay(QWidget):
    region_selected = Signal(QImage)
    window_selected = Signal(int)
    fullscreen_selected = Signal()
    cancelled = Signal()

    def __init__(self, screenshot: QImage, geometry: QRect, windows: list[WindowInfo]) -> None:
        super().__init__()
        self._screenshot = screenshot
        self._pixmap = QPixmap.fromImage(screenshot)
        self._geometry = geometry
        self._windows = windows
        # Window bounds are global; the overlay's coordinates are relative to the
        # virtual desktop origin, so shift them once for hit-testing and drawing.
        self._boxes = [
            (w.bounds.x - geometry.x(), w.bounds.y - geometry.y(), w.bounds.width, w.bounds.height)
            for w in windows
        ]
        # Ratio between native screenshot pixels and logical overlay points.
        self._sx = screenshot.width() / max(geometry.width(), 1)
        self._sy = screenshot.height() / max(geometry.height(), 1)

        self._hover: int | None = None  # window under the pointer, or None for full screen
        self._origin = None
        self._current = None
        self._dragging = False
        self._press_hover: int | None = None
        self._activated = False

        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.setGeometry(geometry)

    def changeEvent(self, event) -> None:
        # If something steals focus while the overlay is up — a hot corner firing
        # Mission Control / App Exposé, Cmd-Tab, a click elsewhere — cancel
        # instead of leaving a dimmed, screen-covering window the user can't
        # dismiss (Esc only works while we hold keyboard focus).
        if event.type() == QEvent.ActivationChange:
            if self.isActiveWindow():
                self._activated = True
            elif self._activated:
                self._cancel()
        super().changeEvent(event)

    # --- painting ---------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), self._pixmap)
        painter.fillRect(self.rect(), _DIM)

        if self._dragging and self._origin is not None and self._current is not None:
            self._paint_region(painter)
        elif self._hover is not None:
            self._paint_window(painter)
        else:
            self._paint_fullscreen(painter)

    def _paint_region(self, painter: QPainter) -> None:
        sel = self._selection()
        source = QRectF(
            *scale_rect((sel.x(), sel.y(), sel.width(), sel.height()), self._sx, self._sy)
        )
        painter.drawPixmap(QRectF(sel), self._pixmap, source)
        painter.setPen(QPen(_ACCENT, 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(sel)
        self._draw_size_label(painter, sel, source)

    def _paint_window(self, painter: QPainter) -> None:
        bx, by, bw, bh = self._boxes[self._hover]
        sel = QRect(int(bx), int(by), int(bw), int(bh))
        source = QRectF(*scale_rect((bx, by, bw, bh), self._sx, self._sy))
        # Restore the hovered window to full brightness, then outline it.
        painter.drawPixmap(QRectF(sel), self._pixmap, source)
        painter.setPen(QPen(_ACCENT, 3))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(sel)
        self._draw_window_label(painter, sel, self._windows[self._hover])

    def _paint_fullscreen(self, painter: QPainter) -> None:
        # Pointer is over empty space: restore the whole desktop to full
        # brightness and outline it so a click clearly means "full screen".
        rect = self.rect()
        painter.drawPixmap(rect, self._pixmap)
        painter.setPen(QPen(_ACCENT, 3))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect.adjusted(1, 1, -2, -2))
        self._draw_hint(painter)

    def _draw_size_label(self, painter: QPainter, sel: QRect, source: QRectF) -> None:
        label = f"{int(source.width())} × {int(source.height())}"
        painter.setFont(QFont("", 12))
        text_w = painter.fontMetrics().horizontalAdvance(label) + 12
        box = QRect(sel.x(), max(sel.y() - 24, 2), text_w, 20)
        painter.fillRect(box, QColor(0, 0, 0, 160))
        painter.setPen(Qt.white)
        painter.drawText(box, Qt.AlignCenter, label)

    def _draw_window_label(self, painter: QPainter, sel: QRect, window: WindowInfo) -> None:
        text = window.owner
        if window.title:
            text = f"{window.owner} · {window.title}"
        painter.setFont(QFont("", 12))
        text_w = painter.fontMetrics().horizontalAdvance(text) + 16
        box = QRect(sel.x(), max(sel.y() - 26, 2), min(text_w, sel.width() or text_w), 22)
        painter.fillRect(box, QColor(0, 0, 0, 180))
        painter.setPen(Qt.white)
        painter.drawText(box.adjusted(8, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft, text)

    def _draw_hint(self, painter: QPainter) -> None:
        hint = t("smart.hint")
        painter.setFont(QFont("", 14))
        metrics = painter.fontMetrics()
        box = QRect(0, 0, metrics.horizontalAdvance(hint) + 32, 40)
        box.moveCenter(self.rect().center())
        painter.fillRect(box, QColor(0, 0, 0, 180))
        painter.setPen(Qt.white)
        painter.drawText(box, Qt.AlignCenter, hint)

    # --- interaction ------------------------------------------------------

    def _selection(self) -> QRect:
        x, y, w, h = selection_rect(
            self._origin.x(), self._origin.y(), self._current.x(), self._current.y()
        )
        return QRect(int(x), int(y), int(w), int(h))

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        if self._origin is not None:
            self._current = pos
            if not self._dragging:
                dx = pos.x() - self._origin.x()
                dy = pos.y() - self._origin.y()
                if (dx * dx + dy * dy) ** 0.5 > _DRAG_THRESHOLD:
                    self._dragging = True
            self.update()
            return
        hover = window_at_point(self._boxes, pos.x(), pos.y())
        if hover != self._hover:
            self._hover = hover
            self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.RightButton:
            self._cancel()
            return
        if event.button() == Qt.LeftButton:
            self._origin = event.position()
            self._current = event.position()
            self._dragging = False
            self._press_hover = self._hover
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton or self._origin is None:
            return
        self._current = event.position()
        if self._dragging:
            self._accept_region()
        else:
            self._accept_target(self._press_hover)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self._cancel()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self._dragging:
                self._accept_region()
            else:
                self._accept_target(self._hover)

    def _accept_region(self) -> None:
        sel = self._selection()
        if sel.width() < _MIN_SIZE or sel.height() < _MIN_SIZE:
            self._cancel()
            return
        phys = scale_rect((sel.x(), sel.y(), sel.width(), sel.height()), self._sx, self._sy)
        cropped = self._screenshot.copy(QRect(*phys))
        self.region_selected.emit(cropped)
        self.close()

    def _accept_target(self, hover: int | None) -> None:
        if hover is not None:
            self.window_selected.emit(self._windows[hover].window_id)
        else:
            self.fullscreen_selected.emit()
        self.close()

    def _cancel(self) -> None:
        self.cancelled.emit()
        self.close()
