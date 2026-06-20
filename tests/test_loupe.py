# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the shared pixel loupe (``ui.loupe.paint_loupe``).

The loupe is a free function the capture overlay and the spotlight editor both
borrow to draw a magnified, crosshaired patch under the pointer. These pin its
behavioural contract — it paints onto the borrowed painter, clamps the sampled
pixel to the image on edge hovering, and never raises — without asserting exact
pixels (anti-aliased text/lines aren't worth pinning).
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap

from shotquill.ui.loupe import paint_loupe


def _paint(cursor, *, src_w=10, src_h=8, bound=300):
    """Paint the loupe for ``cursor`` onto a black surface; return that surface."""
    src = QImage(src_w, src_h, QImage.Format.Format_RGB32)
    src.fill(QColor(10, 20, 30))
    src.setPixelColor(src_w // 2, src_h // 2, QColor(200, 100, 50))
    pixmap = QPixmap.fromImage(src)

    surface = QImage(bound, bound, QImage.Format.Format_ARGB32)
    surface.fill(QColor(0, 0, 0))
    painter = QPainter(surface)
    paint_loupe(
        painter,
        pixmap=pixmap,
        image=src,
        cursor=cursor,
        sx=src_w / bound,
        sy=src_h / bound,
        bound_w=bound,
        bound_h=bound,
        accent=QColor(255, 0, 0),
        font=QFont(),
    )
    painter.end()
    return surface


def _has_painted_pixels(image: QImage) -> bool:
    black = QColor(0, 0, 0)
    return any(
        image.pixelColor(x, y) != black
        for x in range(0, image.width(), 4)
        for y in range(0, image.height(), 4)
    )


def test_draws_onto_the_borrowed_painter(qapp):
    surface = _paint(QPointF(150, 150))
    assert _has_painted_pixels(surface)


def test_clamps_to_image_on_edge_hovering(qapp):
    # Cursor in the far corner maps past the last pixel; the loupe must clamp the
    # sampled pixel into range rather than index out of bounds or raise.
    surface = _paint(QPointF(299, 299))
    assert _has_painted_pixels(surface)


def test_handles_top_left_corner(qapp):
    surface = _paint(QPointF(0, 0))
    assert _has_painted_pixels(surface)
