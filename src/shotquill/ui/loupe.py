# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Shared pixel loupe: a magnified patch that follows the pointer.

Both the capture overlay (initial region selection) and the spotlight editor
(crop-edge adjustment) draw this so a region boundary can be placed on an exact
pixel. The patch is a native-resolution slice blown up without smoothing, so
individual pixels stay crisp, with a crosshair over the centre pixel and a
readout of its coordinates and colour. Keeping it here means the two surfaces
can never drift apart visually.

It is a free function (not a widget): each surface already owns a frozen
screenshot and a ``paintEvent``, so the loupe just borrows the active painter.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap

from shotquill.ui.geometry import loupe_anchor

LOUPE_W = 120  # loupe display size, logical points
LOUPE_H = 90
LOUPE_ZOOM = 4  # one native pixel becomes a 4x4-point block inside the loupe
LOUPE_OFFSET = 20  # gap between the pointer and the loupe
LOUPE_LABEL_H = 20  # readout strip under the magnified pixels


def paint_loupe(
    painter: QPainter,
    *,
    pixmap: QPixmap,
    image: QImage,
    cursor: QPointF,
    sx: float,
    sy: float,
    bound_w: float,
    bound_h: float,
    accent: QColor,
    font: QFont,
) -> None:
    """Draw the loupe magnifying the pixel under ``cursor``.

    ``pixmap``/``image`` are the same frozen shot at native resolution (the
    pixmap is the magnified source; the image supplies the colour/coord
    readout). ``cursor`` is in logical points within a ``bound_w``x``bound_h``
    surface; ``sx``/``sy`` convert those to native pixels. ``accent`` colours the
    crosshair, ``font`` is the surface's UI font (the readout derives its size
    from it, keeping the labels on the real UI face rather than a Qt fallback).
    """
    cx, cy = cursor.x(), cursor.y()
    # Native pixel under the pointer (clamped so edge hovering stays valid).
    px = min(max(int(cx * sx), 0), image.width() - 1)
    py = min(max(int(cy * sy), 0), image.height() - 1)
    ax, ay = loupe_anchor(cx, cy, LOUPE_W, LOUPE_H + LOUPE_LABEL_H, bound_w, bound_h, LOUPE_OFFSET)
    view = QRectF(ax, ay, LOUPE_W, LOUPE_H)

    # A native-resolution patch centred on the pointer, blown up without
    # smoothing so individual pixels (and thus the exact region boundary) stay
    # visible. Near screen edges the patch is clamped to the shot and the
    # remainder left dark.
    painter.fillRect(view, QColor(0, 0, 0, 220))
    src_w = LOUPE_W / LOUPE_ZOOM
    src_h = LOUPE_H / LOUPE_ZOOM
    source = QRectF(px + 0.5 - src_w / 2, py + 0.5 - src_h / 2, src_w, src_h)
    clamped = source.intersected(QRectF(0, 0, pixmap.width(), pixmap.height()))
    if not clamped.isEmpty():
        target = QRectF(
            ax + (clamped.x() - source.x()) * LOUPE_ZOOM,
            ay + (clamped.y() - source.y()) * LOUPE_ZOOM,
            clamped.width() * LOUPE_ZOOM,
            clamped.height() * LOUPE_ZOOM,
        )
        painter.drawPixmap(target, pixmap, clamped)

    # Crosshair over the centre pixel, then a frame around the loupe.
    painter.setPen(QPen(accent, 1))
    center = view.center()
    painter.drawLine(QPointF(view.left(), center.y()), QPointF(view.right(), center.y()))
    painter.drawLine(QPointF(center.x(), view.top()), QPointF(center.x(), view.bottom()))
    painter.setPen(QPen(QColor(255, 255, 255, 200), 1))
    painter.setBrush(Qt.NoBrush)
    painter.drawRect(view)

    # Pointer position (native pixels) and the colour under it.
    color = image.pixelColor(px, py)
    label = f"({px}, {py})  {color.name().upper()}"
    box = QRectF(ax, ay + LOUPE_H, LOUPE_W, LOUPE_LABEL_H)
    painter.fillRect(box, QColor(0, 0, 0, 200))
    label_font = QFont(font)
    label_font.setPointSize(10)
    painter.setFont(label_font)
    painter.setPen(Qt.white)
    painter.drawText(box, Qt.AlignCenter, label)
