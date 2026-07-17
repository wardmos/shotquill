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
    def __init__(self, geo=None):
        self._geo = geo if geo is not None else _SCREEN

    def geometry(self):
        return self._geo

    def availableGeometry(self):
        return self._geo


def _image(width, height, color="white") -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    return image


def _surface(qtbot, config, monkeypatch, *, origin=None, screenshot=None, show=True, screens=None):
    monkeypatch.setattr(QGuiApplication, "screenAt", lambda pt: _FakeScreen())
    monkeypatch.setattr(QGuiApplication, "primaryScreen", lambda: _FakeScreen())
    monkeypatch.setattr(QGuiApplication, "screens", lambda: screens or [_FakeScreen()])
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


def test_escape_in_color_dialog_closes_the_entire_surface(qtbot, config, monkeypatch):
    # The full-screen shell needs the same session-level Escape behaviour as the
    # framed editor even while a child top-level owns keyboard focus.
    surface = _surface(qtbot, config, monkeypatch)
    dialog = surface._toolbar.color_dialog
    dialog.show()
    assert surface.isVisible() and dialog.isVisible()

    qtbot.keyClick(dialog, Qt.Key_Escape)

    assert not surface.isVisible()
    assert not dialog.isVisible()


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


def test_copy_and_save_stay_visible_when_the_floating_tool_row_overflows(
    qtbot, config, monkeypatch
):
    # The full annotation row is wider than this screen. Copy/save must live on
    # their own no-collapse bar instead of trailing the row and folding first.
    surface = _surface(qtbot, config, monkeypatch)
    toolbar = surface._toolbar
    outputs = toolbar.outputs_toolbar

    surface.resize(200, surface.height())
    surface._reposition_toolbar()
    assert toolbar.width() > surface.width()
    assert outputs.parent() is surface
    assert outputs.isVisible()
    assert surface.rect().contains(outputs.geometry().topLeft())
    assert outputs.widgetForAction(surface._copy_action).isVisible()
    assert outputs.widgetForAction(surface._save_action).isVisible()


def test_non_region_surface_is_pure_dim(qtbot, config, monkeypatch):
    # A capture with no desktop context falls back to the plain dim layer.
    monkeypatch.setattr(QGuiApplication, "screenAt", lambda pt: _FakeScreen())
    monkeypatch.setattr(QGuiApplication, "primaryScreen", lambda: _FakeScreen())
    monkeypatch.setattr(QGuiApplication, "screens", lambda: [_FakeScreen()])
    surface = SpotlightSurface(_image(200, 120), config, QRect(1100, 580, 200, 120), None)
    surface.setAttribute(Qt.WA_DeleteOnClose, False)
    qtbot.addWidget(surface)
    surface.show()
    qtbot.waitExposed(surface)
    assert surface.crop_adjustable() is False
    assert surface._screen_pixmap is None


def test_non_adjustable_surface_keeps_dimmed_desktop_context(qtbot, config, monkeypatch):
    # Window captures are not crop-adjustable, but their spotlight backdrop still
    # needs the frozen desktop slice so the area outside the window stays visible.
    monkeypatch.setattr(QGuiApplication, "screenAt", lambda pt: _FakeScreen())
    monkeypatch.setattr(QGuiApplication, "primaryScreen", lambda: _FakeScreen())
    monkeypatch.setattr(QGuiApplication, "screens", lambda: [_FakeScreen()])
    shot = _image(_SCREEN.width(), _SCREEN.height(), "red")
    region = RegionContext(shot, QRect(_SCREEN), adjustable=False)
    surface = SpotlightSurface(_image(200, 120), config, QRect(1100, 580, 200, 120), region)
    surface.setAttribute(Qt.WA_DeleteOnClose, False)
    qtbot.addWidget(surface)
    surface.show()
    qtbot.waitExposed(surface)

    assert surface.crop_adjustable() is False
    assert surface._screen_pixmap is not None
    image = surface.grab().toImage()
    outside = image.pixelColor(20, 20)
    assert outside.red() > 0
    assert outside.green() == 0
    assert outside.blue() == 0
    assert outside.red() < QColor("red").red()


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


def test_drag_tracks_the_cursor_for_the_loupe(qtbot, config, monkeypatch):
    surface = _surface(qtbot, config, monkeypatch)
    assert surface._cursor is None  # no loupe at rest
    surface._try_begin_handle_drag(QPointF(300, 140))  # right-edge midpoint (local)
    surface._update_handle_drag(QPointF(340, 150))
    assert surface._cursor is not None
    assert (surface._cursor.x(), surface._cursor.y()) == (340, 150)
    surface._end_handle_drag(QPointF(340, 150))
    assert surface._cursor is None  # drag over -> loupe gone


def test_drag_updates_only_dirty_regions(qtbot, config, monkeypatch):
    surface = _surface(qtbot, config, monkeypatch)
    updates = []
    monkeypatch.setattr(surface, "update", lambda region=None: updates.append(region))

    surface._try_begin_handle_drag(QPointF(300, 140))
    surface._update_handle_drag(QPointF(340, 150))

    assert updates
    assert all(region is not None for region in updates)
    assert all(region.boundingRect() != surface.rect() for region in updates)


def test_paint_loupe_mid_drag_does_not_crash(qtbot, config, monkeypatch):
    from PySide6.QtGui import QPainter, QPixmap

    surface = _surface(qtbot, config, monkeypatch)
    surface._try_begin_handle_drag(QPointF(300, 140))
    surface._update_handle_drag(QPointF(395, 295))  # near the surface corner -> loupe flips/clamps
    canvas = QPixmap(_SCREEN.width(), _SCREEN.height())
    painter = QPainter(canvas)
    surface.render(canvas)  # full mid-drag paint path, loupe included
    painter.end()


def test_non_region_surface_paints_no_loupe(qtbot, config, monkeypatch):
    # A non-region capture has no frozen slice to magnify: the loupe stays away
    # even if a stray cursor/drag state is set.
    monkeypatch.setattr(QGuiApplication, "screenAt", lambda pt: _FakeScreen())
    monkeypatch.setattr(QGuiApplication, "primaryScreen", lambda: _FakeScreen())
    monkeypatch.setattr(QGuiApplication, "screens", lambda: [_FakeScreen()])
    surface = SpotlightSurface(_image(200, 120), config, QRect(1100, 580, 200, 120), None)
    surface.setAttribute(Qt.WA_DeleteOnClose, False)
    qtbot.addWidget(surface)
    assert surface._screen_image is None
    surface._cursor = QPointF(50, 50)
    from PySide6.QtGui import QPainter

    painter = QPainter(surface)
    surface._paint_loupe(painter)  # guarded by the None pixmap -> no crash
    painter.end()


def test_hover_over_an_edge_shows_a_resize_cursor(qtbot, config, monkeypatch):
    # Bug fix: hovering a crop edge must show a resize cursor (and the view must
    # not reset it back to the default on the same hover move).
    surface = _surface(qtbot, config, monkeypatch)
    assert surface._update_hover_cursor(QPointF(300, 140)) is True  # right-edge midpoint
    assert surface._canvas.viewport().cursor().shape() == Qt.SizeHorCursor
    assert surface._update_hover_cursor(QPointF(200, 140)) is False  # interior → no resize cursor


def test_viewport_resize_refits_the_shot(qtbot, config, monkeypatch):
    # Bug fix: re-fit after the viewport actually resizes, not straight after
    # setGeometry (which can fit against the stale size and leave grey margins).
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QResizeEvent

    surface = _surface(qtbot, config, monkeypatch)
    calls = []
    monkeypatch.setattr(surface._canvas, "fitInView", lambda *a, **k: calls.append(True))
    surface.eventFilter(surface._canvas.viewport(), QResizeEvent(QSize(240, 120), QSize(200, 120)))
    assert calls  # the viewport resize re-fit the shot


def test_cover_menubar_raises_above_the_menu_bar_on_macos(qtbot, config, monkeypatch):
    # Bug fix: the surface must cover the system menu bar (like the capture
    # overlay) so editing isn't obscured and the crop can be dragged to the top.
    import shotquill.ui.spotlight as sp

    monkeypatch.setattr(sp.sys, "platform", "darwin")
    surface = _surface(qtbot, config, monkeypatch)
    calls = []
    monkeypatch.setattr(sp.macos_window, "raise_above_menubar", lambda w: calls.append(w) or True)
    surface._cover_menubar()
    assert calls == [surface]


def test_reactivation_re_covers_the_menu_bar(qtbot, config, monkeypatch):
    # macOS re-shows the menu bar on activation; the surface re-covers it.
    from PySide6.QtCore import QEvent

    import shotquill.ui.spotlight as sp

    monkeypatch.setattr(sp.sys, "platform", "darwin")
    surface = _surface(qtbot, config, monkeypatch)
    calls = []
    monkeypatch.setattr(sp.macos_window, "raise_above_menubar", lambda w: calls.append(w) or True)
    monkeypatch.setattr(surface, "isActiveWindow", lambda: True)
    surface.changeEvent(QEvent(QEvent.ActivationChange))
    assert calls == [surface]


def test_reactivation_does_not_interrupt_active_text_edit(qtbot, config, monkeypatch):
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QGraphicsTextItem

    surface = _surface(qtbot, config, monkeypatch)
    surface._canvas.setFocus()
    surface._canvas._create_text(QPointF(20, 20))
    item = next(
        entry for entry in surface._canvas.scene().items() if isinstance(entry, QGraphicsTextItem)
    )
    assert surface._canvas.scene().focusItem() is item
    monkeypatch.setattr(surface, "isActiveWindow", lambda: True)
    focus_calls = []
    monkeypatch.setattr(surface, "setFocus", lambda: focus_calls.append(True))

    surface.changeEvent(QEvent(QEvent.ActivationChange))

    assert focus_calls == []
    assert item.scene() is surface._canvas.scene()
    assert item.committed is False
    assert surface._canvas.scene().focusItem() is item


def test_handles_sit_outside_the_selection(qtbot, config, monkeypatch):
    # The eight handles must sit just outside the selection so the canvas child
    # (which fills the selection exactly) can't cover them — fully visible.
    surface = _surface(qtbot, config, monkeypatch)
    sel = QRectF(100, 80, 200, 120)
    rects = surface._handle_rects(sel)
    assert len(rects) == 8
    for r in rects:
        assert not sel.contains(r.center())  # every handle centre is outside


def test_size_text_is_native_pixels(qtbot, config, monkeypatch):
    surface = _surface(qtbot, config, monkeypatch)  # region geometry == screen, sx = sy = 1
    assert surface._size_text(QRectF(0, 0, 240, 120)) == "240 × 120"


def test_single_screen_has_no_dim_layers(qtbot, config, monkeypatch):
    surface = _surface(qtbot, config, monkeypatch, show=False)  # one screen
    assert surface._dim_screens == []


def test_dims_only_the_other_screens(qtbot, config, monkeypatch):
    other = QRect(1400, 500, 300, 200)
    surface = _surface(
        qtbot, config, monkeypatch, show=False, screens=[_FakeScreen(), _FakeScreen(other)]
    )
    assert len(surface._dim_screens) == 1  # the selection's screen is the surface itself
    assert surface._dim_screens[0].geometry() == other


def test_dim_layers_hide_when_the_surface_deactivates(qtbot, config, monkeypatch):
    from PySide6.QtCore import QEvent

    other = QRect(1400, 500, 300, 200)
    surface = _surface(qtbot, config, monkeypatch, screens=[_FakeScreen(), _FakeScreen(other)])
    dim = surface._dim_screens[0]
    assert dim.isVisible()  # shown with the surface
    monkeypatch.setattr(surface, "isActiveWindow", lambda: False)
    surface.changeEvent(QEvent(QEvent.ActivationChange))
    assert not dim.isVisible()  # must not darken whatever the user switched to


def test_arrow_nudge_is_bounded_to_the_surface_screen(qtbot, config, monkeypatch):
    # Virtual desktop wider than this surface's screen (a second screen to the
    # right): _SCREEN is its left half. A keyboard nudge must keep the crop on
    # the surface's own screen, not push it onto the neighbour — which would
    # slide the canvas child out of this window.
    desktop = QRect(1000, 500, 800, 300)
    monkeypatch.setattr(QGuiApplication, "screenAt", lambda pt: _FakeScreen())
    monkeypatch.setattr(QGuiApplication, "primaryScreen", lambda: _FakeScreen())
    monkeypatch.setattr(QGuiApplication, "screens", lambda: [_FakeScreen()])
    shot = _image(desktop.width(), desktop.height())
    origin = QRect(1100, 580, 200, 120)
    crop = shot.copy(origin.translated(-desktop.topLeft()))
    surface = SpotlightSurface(crop, config, origin, RegionContext(shot, desktop))
    surface.setAttribute(Qt.WA_DeleteOnClose, False)
    qtbot.addWidget(surface)
    surface.show()
    qtbot.waitExposed(surface)
    for _ in range(60):  # hammer right; unbounded this would reach the desktop edge (1800)
        qtbot.keyClick(surface, Qt.Key_Right, Qt.ShiftModifier)
    assert surface._origin.right() <= _SCREEN.right()  # pinned at the surface's screen edge
