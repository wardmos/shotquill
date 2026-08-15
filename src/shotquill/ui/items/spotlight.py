# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Spotlight annotations that dim the screenshot outside shaped focus regions."""

from __future__ import annotations

import weakref

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QGraphicsItem, QGraphicsPathItem

from shotquill.ui.items.rounded_rect import rounded_rect_path

_DIM_ALPHA = 150


class SpotlightOverlayItem(QGraphicsItem):
    """One shared dimming mask with holes for every active spotlight region."""

    def __init__(self, scene_rect: QRectF) -> None:
        super().__init__()
        self._scene_rect = QRectF(scene_rect)
        self._regions: list[SpotlightRegionItem] = []
        self.setAcceptedMouseButtons(Qt.NoButton)

    def boundingRect(self) -> QRectF:  # noqa: N802 (Qt override)
        return QRectF(self._scene_rect)

    def shape(self) -> QPainterPath:
        # The overlay is visual infrastructure, never an interaction target.
        return QPainterPath()

    def set_scene_rect(self, scene_rect: QRectF) -> None:
        self.prepareGeometryChange()
        self._scene_rect = QRectF(scene_rect)
        self.update()

    def add_region(self, region: SpotlightRegionItem) -> None:
        if region not in self._regions:
            self._regions.append(region)
            self.update()

    def remove_region(self, region: SpotlightRegionItem) -> None:
        if region in self._regions:
            self._regions.remove(region)
            self.update()

    def paint(self, painter: QPainter, option, widget=None) -> None:
        active = [
            region
            for region in self._regions
            if region.scene() is self.scene() and not region.path().isEmpty()
        ]
        if not active:
            return

        outer = QPainterPath()
        outer.addRect(self._scene_rect)
        holes = QPainterPath()
        for region in active:
            holes = holes.united(region.mapToItem(self, region.path()))

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillPath(outer.subtracted(holes), QColor(0, 0, 0, _DIM_ALPHA))
        painter.restore()


class SpotlightRegionItem(QGraphicsPathItem):
    """An invisible movable shape that cuts a hole in the shared dim mask."""

    def __init__(
        self, overlay: SpotlightOverlayItem, *, ellipse: bool = False, rounded: bool = False
    ) -> None:
        super().__init__()
        self._overlay_ref = weakref.ref(overlay)
        self._ellipse = ellipse
        self._rounded = rounded
        self._rect = QRectF()
        overlay.add_region(self)

    def rect(self) -> QRectF:
        return QRectF(self._rect)

    def setRect(self, rect: QRectF) -> None:  # noqa: N802 (Qt-compatible API)
        self._rect = QRectF(rect).normalized()
        path = QPainterPath()
        if self._ellipse:
            path.addEllipse(self._rect)
        elif self._rounded:
            path = rounded_rect_path(self._rect)
        else:
            path.addRect(self._rect)
        self.setPath(path)
        self._update_overlay()

    def shape(self) -> QPainterPath:
        return QPainterPath(self.path())

    def paint(self, painter: QPainter, option, widget=None) -> None:
        # Selection feedback is drawn by AnnotationCanvas.drawForeground; the
        # region itself stays transparent so exported pixels inside remain exact.
        pass

    def itemChange(self, change, value):  # noqa: N802 (Qt override)
        result = super().itemChange(change, value)
        if change in (QGraphicsItem.ItemPositionHasChanged, QGraphicsItem.ItemSceneHasChanged):
            self._update_overlay()
        return result

    def _update_overlay(self) -> None:
        overlay = self._overlay_ref()
        if overlay is not None:
            overlay.update()
