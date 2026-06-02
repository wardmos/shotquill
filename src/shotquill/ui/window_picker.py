# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Full-screen window-picker overlay.

Shows a frozen, dimmed screenshot of the whole virtual desktop. As the pointer
moves, the application window under it is lit up and outlined; a click emits that
window's id (the app then captures the live window by id). Esc or a right-click
cancels. This mirrors macOS's own ``⌘⇧4`` + Space window-shot interaction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from shotquill.i18n import t
from shotquill.ui.geometry import scale_rect, window_at_point

if TYPE_CHECKING:
    from PySide6.QtGui import QImage

    from shotquill.capture.base import WindowInfo

_DIM = QColor(0, 0, 0, 120)
_ACCENT = QColor("#2d7ff9")


class WindowPicker(QWidget):
    window_selected = Signal(int)
    cancelled = Signal()

    def __init__(self, screenshot: QImage, geometry: QRect, windows: list[WindowInfo]) -> None:
        super().__init__()
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
        self._hover: int | None = None

        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.setGeometry(geometry)

    # --- painting ---------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), self._pixmap)
        painter.fillRect(self.rect(), _DIM)

        if self._hover is None:
            self._draw_hint(painter)
            return

        bx, by, bw, bh = self._boxes[self._hover]
        sel = QRect(int(bx), int(by), int(bw), int(bh))
        source = QRectF(*scale_rect((bx, by, bw, bh), self._sx, self._sy))
        # Restore the hovered window to full brightness, then outline it.
        painter.drawPixmap(QRectF(sel), self._pixmap, source)
        painter.setPen(QPen(_ACCENT, 3))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(sel)
        self._draw_label(painter, sel, self._windows[self._hover])

    def _draw_label(self, painter: QPainter, sel: QRect, window: WindowInfo) -> None:
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
        hint = t("picker.hint")
        painter.setFont(QFont("", 14))
        metrics = painter.fontMetrics()
        box = QRect(0, 0, metrics.horizontalAdvance(hint) + 32, 40)
        box.moveCenter(self.rect().center())
        painter.fillRect(box, QColor(0, 0, 0, 180))
        painter.setPen(Qt.white)
        painter.drawText(box, Qt.AlignCenter, hint)

    # --- interaction ------------------------------------------------------

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        hover = window_at_point(self._boxes, pos.x(), pos.y())
        if hover != self._hover:
            self._hover = hover
            self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.RightButton:
            self._cancel()
            return
        if event.button() == Qt.LeftButton and self._hover is not None:
            window_id = self._windows[self._hover].window_id
            self.window_selected.emit(window_id)
            self.close()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self._cancel()

    def _cancel(self) -> None:
        self.cancelled.emit()
        self.close()
