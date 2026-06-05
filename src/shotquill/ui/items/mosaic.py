# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Mosaic (pixelation) redaction item.

Samples a region of the background screenshot, pixelates it by scaling down and
back up with nearest-neighbor, and draws it on top — so the exported image hides
the original pixels.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QGraphicsPixmapItem

_DEFAULT_BLOCK = 12


def pixelate(source: QPixmap, block: int = _DEFAULT_BLOCK) -> QPixmap:
    """Return a blocky (mosaic) copy of ``source`` with ~``block``-pixel cells."""
    if source.isNull() or source.width() < 1 or source.height() < 1:
        return source
    small = source.scaled(
        max(1, source.width() // block),
        max(1, source.height() // block),
        Qt.IgnoreAspectRatio,
        Qt.FastTransformation,
    )
    return small.scaled(
        source.width(),
        source.height(),
        Qt.IgnoreAspectRatio,
        Qt.FastTransformation,
    )


class MosaicItem(QGraphicsPixmapItem):
    """A pixelated copy of a background region, used to redact sensitive content."""

    def __init__(self, background: QPixmap, block: int = _DEFAULT_BLOCK) -> None:
        super().__init__()
        self._background = background
        self._block = block
        self._has_region = False

    def has_region(self) -> bool:
        """Whether the item currently shows a valid pixelated region."""
        return self._has_region

    def update_rect(self, rect: QRect) -> None:
        rect = rect.intersected(self._background.rect())
        if rect.width() < 1 or rect.height() < 1:
            # The drag left the background entirely: clear any earlier pixmap
            # so a stale mosaic can't survive into undo/export.
            self.setPixmap(QPixmap())
            self._has_region = False
            return
        self.setPixmap(pixelate(self._background.copy(rect), self._block))
        self.setPos(rect.topLeft())
        self._has_region = True
