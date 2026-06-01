# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""An arrow: a line with a filled triangular head at its end point."""

from __future__ import annotations

from PySide6.QtCore import QPointF
from PySide6.QtGui import QBrush, QPolygonF
from PySide6.QtWidgets import QGraphicsLineItem

from shotquill.ui.items.geometry import arrowhead_points


class ArrowItem(QGraphicsLineItem):
    def boundingRect(self):  # noqa: N802 (Qt override)
        extra = self.pen().widthF() + 24
        return super().boundingRect().adjusted(-extra, -extra, extra, extra)

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        line = self.line()
        head_len = max(self.pen().widthF() * 3.0, 14.0)
        left, right = arrowhead_points(line.x1(), line.y1(), line.x2(), line.y2(), length=head_len)
        head = QPolygonF(
            [
                QPointF(line.x2(), line.y2()),
                QPointF(*left),
                QPointF(*right),
            ]
        )
        painter.setBrush(QBrush(self.pen().color()))
        painter.drawPolygon(head)
