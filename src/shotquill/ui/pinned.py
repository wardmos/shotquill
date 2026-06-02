# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""A "pinned" screenshot: a frameless, always-on-top window floating on the desktop.

Pinning keeps an annotated shot visible above other windows for reference. The
window is borderless, draggable anywhere on its surface, and dismissed with Esc
or a double-click. The capture is at physical (Retina) resolution, so we set the
pixmap's device-pixel-ratio to the screen's to show it at its on-screen size,
and scale down anything larger than the available screen so a full-screen pin
still fits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from shotquill.i18n import t

if TYPE_CHECKING:
    from PySide6.QtGui import QImage

_MAX_SCREEN_FRACTION = 0.8


def _fit_pixmap(image: QImage) -> QPixmap:
    """Build a display pixmap: tagged with the screen DPR and capped to the screen."""
    screen = QGuiApplication.primaryScreen()
    dpr = screen.devicePixelRatio() if screen is not None else 1.0
    pixmap = QPixmap.fromImage(image)

    # Logical (point) size the physical pixels would occupy at this DPR.
    logical_w = pixmap.width() / dpr
    logical_h = pixmap.height() / dpr

    if screen is not None:
        avail = screen.availableGeometry().size()
        max_w = avail.width() * _MAX_SCREEN_FRACTION
        max_h = avail.height() * _MAX_SCREEN_FRACTION
        if logical_w > max_w or logical_h > max_h:
            scale = min(max_w / logical_w, max_h / logical_h)
            target = QSize(round(pixmap.width() * scale), round(pixmap.height() * scale))
            pixmap = pixmap.scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    pixmap.setDevicePixelRatio(dpr)
    return pixmap


class PinnedWindow(QWidget):
    """A draggable, always-on-top window showing a pinned screenshot."""

    def __init__(self, image: QImage) -> None:
        super().__init__(
            None,
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool,
        )
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setToolTip(t("pin.tip"))

        self._pixmap = _fit_pixmap(image)
        self.setFixedSize(self._pixmap.deviceIndependentSize().toSize())
        self._drag_offset: QPoint | None = None

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._pixmap)
        # A hairline border so the pin reads as a distinct floating object.
        painter.setPen(QColor(0, 0, 0, 110))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None

    def mouseDoubleClickEvent(self, event) -> None:
        self.close()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)
