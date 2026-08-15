# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""A rounded rectangle annotation and its shared path geometry."""

from __future__ import annotations

from PySide6.QtCore import QRectF
from PySide6.QtGui import QPainterPath
from PySide6.QtWidgets import QGraphicsPathItem

DEFAULT_CORNER_RADIUS = 12.0


def rounded_rect_path(rect: QRectF, radius: float = DEFAULT_CORNER_RADIUS) -> QPainterPath:
    """Return a rounded path whose radius stays valid for small rectangles."""
    normalized = QRectF(rect).normalized()
    corner_radius = max(
        0.0,
        min(float(radius), normalized.width() / 2.0, normalized.height() / 2.0),
    )
    path = QPainterPath()
    path.addRoundedRect(normalized, corner_radius, corner_radius)
    return path


class RoundedRectItem(QGraphicsPathItem):
    """A movable path item with the rect API used by canvas drag tools."""

    def __init__(self, rect: QRectF | None = None) -> None:
        super().__init__()
        self._rect = QRectF()
        if rect is not None:
            self.setRect(rect)

    def rect(self) -> QRectF:
        return QRectF(self._rect)

    def setRect(self, rect: QRectF) -> None:  # noqa: N802 (Qt-compatible API)
        self._rect = QRectF(rect).normalized()
        self.setPath(rounded_rect_path(self._rect))
