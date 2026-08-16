# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Visible progress and cancellation affordance for a long screenshot.

The status HUD stays above normal windows without taking keyboard focus from the
page being scrolled. It is placed outside the selected region whenever the
screen has room. A full-screen selection leaves nowhere safe to draw it, so the
app briefly suspends the HUD around each pixel grab and restores it immediately
afterward; this keeps the status visible between samples without baking it into
the stitched image.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from shotquill.i18n import t

_GAP = 12


def _clamp(value: int, lower: int, upper: int) -> int:
    return max(lower, min(value, max(lower, upper)))


def status_geometry(target: QRect, size: QSize, available: QRect) -> QRect:
    """Place a status window on ``available``, outside ``target`` when possible."""
    width = min(max(size.width(), 1), max(available.width(), 1))
    height = min(max(size.height(), 1), max(available.height(), 1))
    max_x = available.x() + available.width() - width
    max_y = available.y() + available.height() - height
    centered_x = _clamp(target.center().x() - width // 2, available.x(), max_x)
    centered_y = _clamp(target.center().y() - height // 2, available.y(), max_y)

    candidates = (
        QRect(centered_x, target.top() - _GAP - height, width, height),
        QRect(centered_x, target.bottom() + 1 + _GAP, width, height),
        QRect(target.left() - _GAP - width, centered_y, width, height),
        QRect(target.right() + 1 + _GAP, centered_y, width, height),
    )
    for candidate in candidates:
        if available.contains(candidate) and not candidate.intersects(target):
            return candidate

    # A full-screen or nearly full-screen target has no exterior slot. Keep the
    # HUD visible near the top of that screen; the capture loop hides it only
    # for the instant in which pixels are sampled.
    fallback_x = _clamp(target.center().x() - width // 2, available.x(), max_x)
    fallback_y = _clamp(available.top() + _GAP, available.y(), max_y)
    return QRect(fallback_x, fallback_y, width, height)


class ScrollingStatus(QWidget):
    """Small always-on-top progress HUD with an explicit Stop button."""

    stop_requested = Signal()

    def __init__(self, target: QRect, *, available_geometry: QRect | None = None) -> None:
        super().__init__(
            None,
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
            | Qt.NoDropShadowWindowHint,
        )
        self._target = QRect(target)
        screen = QGuiApplication.screenAt(target.center()) or QGuiApplication.primaryScreen()
        self._available = QRect(
            available_geometry
            if available_geometry is not None
            else screen.availableGeometry()
            if screen is not None
            else target
        )
        self._suspended = False

        self.setObjectName("scrollingStatus")
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 9, 9, 9)
        layout.setSpacing(12)
        self._label = QLabel(self)
        self._stop_button = QPushButton(t("scrolling.stop"), self)
        self._stop_button.setCursor(Qt.PointingHandCursor)
        self._stop_button.clicked.connect(self.stop_requested.emit)
        layout.addWidget(self._label, 1)
        layout.addWidget(self._stop_button)

        self.setStyleSheet(
            """
            QLabel {
                color: white;
                font-size: 13px;
            }
            QPushButton {
                color: white;
                background: rgba(255, 255, 255, 34);
                border: 1px solid rgba(255, 255, 255, 58);
                border-radius: 7px;
                padding: 5px 12px;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 54); }
            QPushButton:pressed { background: rgba(255, 255, 255, 24); }
            """
        )
        self.set_progress(0)

    def paintEvent(self, event) -> None:
        # A stylesheet background on a translucent top-level QWidget is not
        # consistently painted by every Qt platform plugin. Draw it explicitly
        # so the progress text always has an opaque, high-contrast surface.
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(255, 255, 255, 46), 1))
        painter.setBrush(QColor(28, 28, 32, 238))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 10, 10)
        painter.end()

    @property
    def target_geometry(self) -> QRect:
        return QRect(self._target)

    def set_progress(self, frames: int) -> None:
        self._label.setText(t("scrolling.status").format(frames=max(int(frames), 0)))
        self.adjustSize()
        self.setGeometry(status_geometry(self._target, self.size(), self._available))

    def present(self) -> None:
        """Show without activating so wheel input keeps reaching the target app."""
        self._suspended = False
        self.adjustSize()
        self.setGeometry(status_geometry(self._target, self.size(), self._available))
        self.show()
        self.raise_()

    def suspend_for_capture(self) -> bool:
        """Hide only when the HUD would otherwise appear in the sampled region."""
        self._suspended = self.isVisible() and self.geometry().intersects(self._target)
        if self._suspended:
            self.hide()
        return self._suspended

    def resume_after_capture(self) -> None:
        if not self._suspended:
            return
        self._suspended = False
        self.show()
        self.raise_()
