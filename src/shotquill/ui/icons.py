# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Vector-drawn toolbar icons.

Every icon is painted with QPainter at call time instead of being shipped as
an asset: a 24×24 logical-pixel monochrome glyph rendered at 2× for Retina
displays, stroked in the palette's text colour so the set follows the system
light/dark appearance for free. Keeping the glyphs as a handful of drawing
primitives also keeps them in version control as reviewable code.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QGuiApplication,
    QIcon,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QPolygonF,
)

# Logical canvas every glyph is designed on; rendered at _SCALE× for HiDPI.
_CANVAS = 24
_SCALE = 2
_STROKE = 1.8

# Logical size the icons are emitted (and shown) at: the glyphs keep their
# 24-grid design coordinates and are scaled down at paint time, so resizing
# the toolbar's icons is a one-constant change here. The toolbar sets its
# icon size to match so the buttons don't pad the glyph back out.
#
# ICON_SIZE suits the stacked "icon over caption" layout, where the label below
# shares the work and a smaller glyph keeps the two-row button compact. Icon-only
# buttons have no caption to lean on, so the glyph carries the whole button and
# reads too small at the stacked size next to native toolbar icons — those use
# the larger standalone size. ``toolbar_icon`` takes the size so each toolbar
# style can pick (see shotquill.ui.toolbar).
ICON_SIZE = 20
ICON_SIZE_STANDALONE = 24

# Stroke width is in 24-grid design units and scales with the icon, so a bigger
# glyph draws thicker. At the standalone size the default stroke looks heavy, so
# icon-only buttons use a thinner one — picked so the on-screen line weight lands
# close to the smaller stacked icons rather than scaling up with the glyph.
ICON_STROKE_STANDALONE = 1.5


def _draw_select(p: QPainter) -> None:
    # Classic pointer: arrow body with a short tail off the heel.
    path = QPainterPath(QPointF(7, 4))
    path.lineTo(7, 17.5)
    path.lineTo(10.4, 14.4)
    path.lineTo(12.6, 19.4)
    path.lineTo(15.0, 18.3)
    path.lineTo(12.8, 13.4)
    path.lineTo(17.4, 13.0)
    path.closeSubpath()
    p.drawPath(path)


def _draw_rect(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(4.5, 6, 15, 12), 1.5, 1.5)


def _draw_ellipse(p: QPainter) -> None:
    p.drawEllipse(QRectF(4.5, 6, 15, 12))


def _draw_arrow(p: QPainter) -> None:
    # Diagonal shaft with an open head, pointing to the top-right.
    p.drawLine(QPointF(5.5, 18.5), QPointF(18, 6))
    p.drawLine(QPointF(11.5, 5.5), QPointF(18.5, 5.5))
    p.drawLine(QPointF(18.5, 5.5), QPointF(18.5, 12.5))


def _draw_line(p: QPainter) -> None:
    p.drawLine(QPointF(5, 19), QPointF(19, 5))


def _draw_pen(p: QPainter) -> None:
    # Pencil at 45°: square body, collar line, sharpened tip.
    p.drawPolygon(
        QPolygonF(
            [
                QPointF(5, 19),
                QPointF(5.7, 15.3),
                QPointF(15.3, 5.7),
                QPointF(18.3, 8.7),
                QPointF(8.7, 18.3),
            ]
        )
    )
    p.drawLine(QPointF(13.2, 7.8), QPointF(16.2, 10.8))


def _draw_highlighter(p: QPainter) -> None:
    # Slanted chisel marker over the band of ink it leaves behind.
    p.drawPolygon(
        QPolygonF(
            [
                QPointF(8.2, 14.8),
                QPointF(14.8, 5.5),
                QPointF(18.5, 9.2),
                QPointF(11.2, 17.2),
                QPointF(8.6, 17.5),
            ]
        )
    )
    band = QPen(p.pen())
    band.setWidthF(3.2)
    p.save()
    p.setPen(band)
    p.drawLine(QPointF(5.5, 20.2), QPointF(14.5, 20.2))
    p.restore()


def _draw_mosaic(p: QPainter) -> None:
    # Checkerboard: the outline plus alternating filled cells.
    p.drawRect(QRectF(5, 5, 14, 14))
    p.save()
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(p.pen().color()))
    for col in range(3):
        for row in range(3):
            if (col + row) % 2 == 0:
                p.fillRect(QRectF(5 + col * 14 / 3, 5 + row * 14 / 3, 14 / 3, 14 / 3), p.brush())
    p.restore()


def _draw_text(p: QPainter) -> None:
    # A serif-less capital T.
    p.drawLine(QPointF(5.5, 6), QPointF(18.5, 6))
    p.drawLine(QPointF(12, 6), QPointF(12, 19))


def _draw_color(p: QPainter) -> None:
    # Paint drop with a small highlight dash inside.
    path = QPainterPath(QPointF(12, 4.2))
    path.cubicTo(QPointF(7, 11), QPointF(5.8, 13.4), QPointF(5.8, 15.4))
    path.arcTo(QRectF(5.8, 9.2, 12.4, 11.2), 180, 180)
    path.cubicTo(QPointF(18.2, 13.4), QPointF(17, 11), QPointF(12, 4.2))
    p.drawPath(path)
    p.drawLine(QPointF(9.4, 15.2), QPointF(10.2, 16.8))


def _draw_undo(p: QPainter) -> None:
    # Three-quarter arc swinging back to an arrowhead on the left.
    path = QPainterPath(QPointF(7.5, 8.5))
    path.arcTo(QRectF(6, 6.5, 12.5, 12.5), 135, -280)
    p.drawPath(path)
    p.drawLine(QPointF(7.6, 4.3), QPointF(7.3, 9.0))
    p.drawLine(QPointF(7.3, 9.0), QPointF(12.0, 9.2))


def _draw_redo(p: QPainter) -> None:
    # _draw_undo mirrored horizontally.
    p.save()
    p.translate(_CANVAS, 0)
    p.scale(-1, 1)
    _draw_undo(p)
    p.restore()


def _draw_ocr(p: QPainter) -> None:
    # Scanner corner brackets framing lines of recognised text.
    for x, dx in ((4.5, 4), (19.5, -4)):
        for y, dy in ((4.5, 4), (19.5, -4)):
            p.drawLine(QPointF(x, y), QPointF(x + dx, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + dy))
    p.drawLine(QPointF(8, 10), QPointF(16, 10))
    p.drawLine(QPointF(8, 14), QPointF(13.5, 14))


def _draw_pin(p: QPainter) -> None:
    # Pushpin at 45°: head, shoulder, and the needle into the corner.
    p.drawPolygon(
        QPolygonF(
            [
                QPointF(13.2, 4.5),
                QPointF(19.5, 10.8),
                QPointF(17.2, 11.5),
                QPointF(14.5, 14.2),
                QPointF(13.9, 17.4),
                QPointF(6.6, 10.1),
                QPointF(9.8, 9.5),
                QPointF(12.5, 6.8),
            ]
        )
    )
    p.drawLine(QPointF(9.4, 14.6), QPointF(5, 19))


def _draw_copy(p: QPainter) -> None:
    # Two offset pages; the back one drawn only where the front doesn't cover.
    path = QPainterPath(QPointF(7.5, 6))
    path.lineTo(7.5, 4.5)
    path.lineTo(17.5, 4.5)
    path.lineTo(17.5, 15.5)
    path.lineTo(16, 15.5)
    p.drawPath(path)
    p.drawRoundedRect(QRectF(5.5, 7.5, 9.5, 11.5), 1, 1)


def _draw_save(p: QPainter) -> None:
    # Arrow descending into a tray (save-to-disk without the floppy).
    p.drawLine(QPointF(12, 4.5), QPointF(12, 14))
    p.drawLine(QPointF(8.2, 10.4), QPointF(12, 14.2))
    p.drawLine(QPointF(15.8, 10.4), QPointF(12, 14.2))
    path = QPainterPath(QPointF(4.5, 14.5))
    path.lineTo(4.5, 19)
    path.lineTo(19.5, 19)
    path.lineTo(19.5, 14.5)
    p.drawPath(path)


_GLYPHS: dict[str, Callable[[QPainter], None]] = {
    "select": _draw_select,
    "rect": _draw_rect,
    "ellipse": _draw_ellipse,
    "arrow": _draw_arrow,
    "line": _draw_line,
    "pen": _draw_pen,
    "highlighter": _draw_highlighter,
    "mosaic": _draw_mosaic,
    "text": _draw_text,
    "color": _draw_color,
    "undo": _draw_undo,
    "redo": _draw_redo,
    "ocr": _draw_ocr,
    "pin": _draw_pin,
    "copy": _draw_copy,
    "save": _draw_save,
}

ICON_NAMES: tuple[str, ...] = tuple(_GLYPHS)


def toolbar_icon(name: str, size: int = ICON_SIZE, stroke: float = _STROKE) -> QIcon:
    """Render the named glyph into a QIcon in the palette's text colour.

    ``size`` is the logical point size the icon is emitted at (the toolbar sets
    its icon size to match); the glyph keeps its 24-grid design and is scaled to
    fit. ``stroke`` is the pen width in those design units, so it scales with the
    glyph; pass a smaller value to keep a larger icon's lines from looking heavy.
    Rendered fresh on every call (toolbars are built once per editor window), so
    a theme change between captures picks up the new palette automatically.
    """
    draw = _GLYPHS[name]
    pixmap = QPixmap(size * _SCALE, size * _SCALE)
    pixmap.setDevicePixelRatio(_SCALE)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    # Glyphs are drawn in their 24-grid design coordinates; this scale maps
    # them onto the emitted size, pen width included.
    painter.scale(size / _CANVAS, size / _CANVAS)
    pen = QPen(QGuiApplication.palette().color(QPalette.Text), stroke)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    draw(painter)
    painter.end()
    return QIcon(pixmap)
