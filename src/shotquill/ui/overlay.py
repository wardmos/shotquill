# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Full-screen region-selection overlay.

Shows a frozen, dimmed screenshot of the whole virtual desktop. The user drags
a rectangle; that region stays bright. Release (or Enter) crops it from the
native-resolution screenshot and emits it; Esc cancels.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from shotquill.ui.geometry import scale_rect, selection_rect

_DIM = QColor(0, 0, 0, 120)
_ACCENT = QColor("#2d7ff9")
_MIN_SIZE = 2


class RegionOverlay(QWidget):
    region_selected = Signal(QImage)
    cancelled = Signal()

    def __init__(self, screenshot: QImage, geometry: QRect) -> None:
        super().__init__()
        self._screenshot = screenshot
        self._pixmap = QPixmap.fromImage(screenshot)
        # Ratio between native screenshot pixels and logical overlay points.
        self._sx = screenshot.width() / max(geometry.width(), 1)
        self._sy = screenshot.height() / max(geometry.height(), 1)
        self._origin = None
        self._current = None
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

        if self._origin is None or self._current is None:
            return

        sel = self._selection()
        source = QRectF(
            *scale_rect((sel.x(), sel.y(), sel.width(), sel.height()), self._sx, self._sy)
        )
        painter.drawPixmap(QRectF(sel), self._pixmap, source)

        painter.setPen(QPen(_ACCENT, 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(sel)
        self._draw_size_label(painter, sel, source)

    def _draw_size_label(self, painter: QPainter, sel: QRect, source: QRectF) -> None:
        label = f"{int(source.width())} × {int(source.height())}"
        painter.setFont(QFont("", 12))
        text_w = painter.fontMetrics().horizontalAdvance(label) + 12
        box = QRect(sel.x(), max(sel.y() - 24, 2), text_w, 20)
        painter.fillRect(box, QColor(0, 0, 0, 160))
        painter.setPen(Qt.white)
        painter.drawText(box, Qt.AlignCenter, label)

    # --- interaction ------------------------------------------------------

    def _selection(self) -> QRect:
        x, y, w, h = selection_rect(
            self._origin.x(), self._origin.y(), self._current.x(), self._current.y()
        )
        return QRect(int(x), int(y), int(w), int(h))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._origin = event.position()
            self._current = event.position()
            self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._origin is not None:
            self._current = event.position()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._origin is not None:
            self._current = event.position()
            self._accept()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self._cancel()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._accept()

    def _accept(self) -> None:
        if self._origin is None or self._current is None:
            self._cancel()
            return
        sel = self._selection()
        if sel.width() < _MIN_SIZE or sel.height() < _MIN_SIZE:
            self._cancel()
            return
        phys = scale_rect((sel.x(), sel.y(), sel.width(), sel.height()), self._sx, self._sy)
        cropped = self._screenshot.copy(QRect(*phys))
        self.region_selected.emit(cropped)
        self.close()

    def _cancel(self) -> None:
        self.cancelled.emit()
        self.close()
