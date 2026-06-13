# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the mosaic (pixelation) redaction helpers."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtGui import QColor, QPixmap  # noqa: E402

from shotquill.ui.items.mosaic import MosaicItem, pixelate  # noqa: E402


def _pixmap(width, height, color="red") -> QPixmap:
    pixmap = QPixmap(width, height)
    pixmap.fill(QColor(color))
    return pixmap


def test_pixelate_preserves_dimensions(qapp):
    out = pixelate(_pixmap(40, 30), 8)
    assert (out.width(), out.height()) == (40, 30)


def test_pixelate_null_pixmap_yields_empty(qapp):
    # A null source has nothing to redact; the result is empty (and never the
    # source carrying pixels, so a region can't leak through verbatim).
    assert pixelate(QPixmap(), 8).isNull()


def test_pixelate_averages_block_no_source_pixel_survives(qapp):
    # A black/white checkerboard collapsed to one cell must average to grey,
    # not pass through a single real pixel (nearest-neighbor would leave pure
    # black or white, weakening the redaction).
    src = QPixmap(2, 2)
    img = src.toImage()
    img.setPixelColor(0, 0, QColor("black"))
    img.setPixelColor(1, 0, QColor("white"))
    img.setPixelColor(0, 1, QColor("white"))
    img.setPixelColor(1, 1, QColor("black"))
    src = QPixmap.fromImage(img)
    out = pixelate(src, 2).toImage()
    for x, y in ((0, 0), (1, 0), (0, 1), (1, 1)):
        value = out.pixelColor(x, y).red()
        assert 0 < value < 255, f"cell ({x},{y}) is a verbatim source pixel: {value}"


def test_pixelate_block_larger_than_image_is_safe(qapp):
    # block > dimension must not divide to zero; output keeps original size.
    out = pixelate(_pixmap(4, 4), 100)
    assert (out.width(), out.height()) == (4, 4)


def test_pixelate_zero_block_does_not_divide_by_zero(qapp):
    # A 0 (or negative) cell size is clamped to 1 instead of raising
    # ZeroDivisionError on the ``width // block`` downscale.
    out = pixelate(_pixmap(40, 30), 0)
    assert (out.width(), out.height()) == (40, 30)


def test_mosaic_item_sets_pixmap_within_background(qapp):
    item = MosaicItem(_pixmap(100, 80))
    item.update_rect(QRect(10, 10, 30, 20))
    assert not item.pixmap().isNull()
    assert item.pos().x() == 10
    assert item.pos().y() == 10


def test_mosaic_item_clamps_rect_to_background(qapp):
    item = MosaicItem(_pixmap(50, 50))
    # Rect partly outside the background gets intersected to the overlap.
    item.update_rect(QRect(40, 40, 100, 100))
    assert item.pixmap().width() <= 10
    assert item.pixmap().height() <= 10


def test_mosaic_item_ignores_empty_rect(qapp):
    item = MosaicItem(_pixmap(50, 50))
    item.update_rect(QRect(0, 0, 0, 0))
    assert item.pixmap().isNull()


def test_mosaic_item_clears_stale_pixmap_when_drag_leaves_background(qapp):
    # A drag that once had a valid region and then leaves the background must
    # not keep showing the old pixelated patch (it would survive into export).
    item = MosaicItem(_pixmap(100, 80))
    item.update_rect(QRect(10, 10, 30, 20))
    assert item.has_region() is True
    item.update_rect(QRect(200, 200, 10, 10))  # no intersection
    assert item.pixmap().isNull()
    assert item.has_region() is False


def test_fresh_mosaic_item_has_no_region(qapp):
    assert MosaicItem(_pixmap(50, 50)).has_region() is False


def test_canvas_treats_regionless_mosaic_as_negligible(qapp):
    from shotquill.ui.canvas import AnnotationCanvas

    item = MosaicItem(_pixmap(100, 80))
    item.update_rect(QRect(10, 10, 30, 20))
    assert AnnotationCanvas._is_negligible(item) is False
    item.update_rect(QRect(200, 200, 10, 10))  # drag ended outside
    assert AnnotationCanvas._is_negligible(item) is True
