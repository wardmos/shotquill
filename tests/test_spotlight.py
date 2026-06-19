# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Headless tests for the unified full-screen spotlight editor surface.

Driven offscreen. A monkeypatched screen with a NON-zero origin is used
throughout so a missing global↔surface-local coordinate translation surfaces as
a failure instead of being masked by a (0,0) origin.
"""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF, QRect, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QUndoCommand  # noqa: E402

from shotquill.ui.editor_core import RegionContext  # noqa: E402
from shotquill.ui.spotlight import SpotlightSurface  # noqa: E402

# This screen sits at a non-zero origin on the virtual desktop.
_SCREEN = QRect(1000, 500, 400, 300)


class _FakeScreen:
    def geometry(self):
        return _SCREEN

    def availableGeometry(self):
        return _SCREEN


def _image(width, height, color="white") -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    return image


def _surface(qtbot, config, monkeypatch, *, origin=None, screenshot=None, show=True):
    monkeypatch.setattr(QGuiApplication, "screenAt", lambda pt: _FakeScreen())
    monkeypatch.setattr(QGuiApplication, "primaryScreen", lambda: _FakeScreen())
    # Region geometry == the screen (sx = sy = 1): surface-local == global - origin.
    origin = origin if origin is not None else QRect(1100, 580, 200, 120)
    shot = screenshot if screenshot is not None else _image(_SCREEN.width(), _SCREEN.height())
    region = RegionContext(shot, QRect(_SCREEN))
    crop = shot.copy(origin.translated(-_SCREEN.topLeft()))
    surface = SpotlightSurface(crop, config, origin, region)
    surface.setAttribute(Qt.WA_DeleteOnClose, False)
    qtbot.addWidget(surface)
    if show:
        surface.show()
        qtbot.waitExposed(surface)
    return surface


def test_surface_covers_the_selections_screen(qtbot, config, monkeypatch):
    surface = _surface(qtbot, config, monkeypatch)
    assert surface.geometry() == _SCREEN


def test_canvas_is_placed_over_the_selection_in_local_coords(qtbot, config, monkeypatch):
    surface = _surface(qtbot, config, monkeypatch)
    # origin (1100,580,200,120) on a screen at (1000,500) → local (100,80,200,120).
    assert surface._canvas.geometry() == QRect(100, 80, 200, 120)
    assert surface._canvas.viewport().size() == QRect(0, 0, 200, 120).size()


def test_drag_right_edge_grows_and_recrops_without_moving_the_window(qtbot, config, monkeypatch):
    screenshot = _image(_SCREEN.width(), _SCREEN.height())
    # Pixel exposed only once the crop widens to x-local 100..340 (global col 100).
    screenshot.setPixelColor(100, 80, QColor("red"))
    surface = _surface(qtbot, config, monkeypatch, screenshot=screenshot)
    window_pos = surface.pos()

    surface._try_begin_handle_drag(QPointF(300, 140))  # right-edge midpoint (local)
    surface._update_handle_drag(QPointF(340, 140))
    surface._end_handle_drag(QPointF(340, 140))

    assert surface.pos() == window_pos  # the top-level window never moved
    assert surface._origin == QRect(1100, 580, 240, 120)
    assert surface._canvas.geometry() == QRect(100, 80, 240, 120)
    background = surface._canvas.background_image()
    assert (background.width(), background.height()) == (240, 120)
    assert background.pixelColor(0, 0) == QColor("red")


def test_drag_left_edge_reveals_more_of_the_shot(qtbot, config, monkeypatch):
    screenshot = _image(_SCREEN.width(), _SCREEN.height())
    screenshot.setPixelColor(60, 80, QColor("red"))  # global col 60, exposed by moving left edge
    surface = _surface(qtbot, config, monkeypatch, screenshot=screenshot)

    surface._try_begin_handle_drag(QPointF(100, 140))  # left-edge midpoint (local)
    surface._update_handle_drag(QPointF(60, 140))
    surface._end_handle_drag(QPointF(60, 140))

    assert surface._origin == QRect(1060, 580, 240, 120)
    assert surface._canvas.background_image().pixelColor(0, 0) == QColor("red")


def test_drag_corner_moves_both_axes(qtbot, config, monkeypatch):
    surface = _surface(qtbot, config, monkeypatch)
    surface._try_begin_handle_drag(QPointF(100, 80))  # top-left corner (local)
    surface._update_handle_drag(QPointF(90, 70))
    surface._end_handle_drag(QPointF(90, 70))
    assert surface._origin == QRect(1090, 570, 210, 130)


def test_drag_clamps_to_the_screen_bounds(qtbot, config, monkeypatch):
    surface = _surface(qtbot, config, monkeypatch)
    surface._try_begin_handle_drag(QPointF(300, 140))
    surface._update_handle_drag(QPointF(9999, 140))  # drag the right edge off-screen
    surface._end_handle_drag(QPointF(9999, 140))
    # Right edge pinned at the screen width (local 400 → global 1400).
    assert surface._origin == QRect(1100, 580, 300, 120)


def test_arrow_nudge_reaches_the_surface_and_recrops(qtbot, config, monkeypatch):
    surface = _surface(qtbot, config, monkeypatch)
    qtbot.keyClick(surface, Qt.Key_Right)
    assert surface._origin == QRect(1101, 580, 200, 120)
    assert surface._canvas.geometry() == QRect(101, 80, 200, 120)


def test_center_press_does_not_start_a_resize(qtbot, config, monkeypatch):
    surface = _surface(qtbot, config, monkeypatch)
    assert surface._try_begin_handle_drag(QPointF(200, 140)) is False  # interior → annotate
    assert surface._drag is None


def test_first_annotation_freezes_the_crop(qtbot, config, monkeypatch):
    surface = _surface(qtbot, config, monkeypatch)
    surface._canvas.undo_stack().push(QUndoCommand())  # an annotation landed
    assert surface.crop_adjustable() is False
    assert surface._try_begin_handle_drag(QPointF(300, 140)) is False  # edge no longer grabs


def test_copy_exports_and_closes(qtbot, config, monkeypatch):
    QGuiApplication.clipboard().clear()
    surface = _surface(qtbot, config, monkeypatch, show=False)
    surface._copy()
    assert not QGuiApplication.clipboard().image().isNull()
    assert not surface.isVisible()


def test_toolbar_floats_as_a_child_near_the_selection(qtbot, config, monkeypatch):
    surface = _surface(qtbot, config, monkeypatch)
    assert surface._toolbar.parent() is surface
    assert surface._toolbar.isVisible()
    # Positioned inside the surface.
    tb = surface._toolbar.geometry()
    assert surface.rect().contains(tb.topLeft())


def test_non_region_surface_is_pure_dim(qtbot, config, monkeypatch):
    # A window/fullscreen capture has no RegionContext: no handles, pure dim.
    monkeypatch.setattr(QGuiApplication, "screenAt", lambda pt: _FakeScreen())
    monkeypatch.setattr(QGuiApplication, "primaryScreen", lambda: _FakeScreen())
    surface = SpotlightSurface(_image(200, 120), config, QRect(1100, 580, 200, 120), None)
    surface.setAttribute(Qt.WA_DeleteOnClose, False)
    qtbot.addWidget(surface)
    surface.show()
    qtbot.waitExposed(surface)
    assert surface.crop_adjustable() is False
    assert surface._screen_pixmap is None


def test_paint_smoke_adjustable_and_dragging(qtbot, config, monkeypatch):
    from PySide6.QtGui import QPainter, QPixmap

    surface = _surface(qtbot, config, monkeypatch)
    canvas = QPixmap(_SCREEN.width(), _SCREEN.height())
    painter = QPainter(canvas)
    surface.render(canvas)  # at rest: handles + dim
    # Mid-drag paint path:
    surface._drag = (False, False, True, False)
    surface._live_sel = QRectF(100, 80, 250, 120)
    surface.render(canvas)
    painter.end()
