# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Headless Qt tests for the full-desktop crop-adjust overlay.

Runs under ``QT_QPA_PLATFORM=offscreen``; exercises edge hit-testing, the
delta-based edge move, clamping, and the commit/cancel outcomes.
"""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402

from shotquill.ui.region_adjust import RegionAdjustOverlay  # noqa: E402


def _image(w, h, color="white"):
    image = QImage(w, h, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    return image


def _overlay(qtbot, selection=None, geometry=None):
    # Native screenshot equals geometry here (sx = sy = 1), so local selection
    # coordinates read straight off the numbers.
    selection = selection or QRect(50, 40, 60, 50)
    geometry = geometry or QRect(0, 0, 200, 150)
    overlay = RegionAdjustOverlay(_image(geometry.width(), geometry.height()), geometry, selection)
    overlay.setAttribute(Qt.WA_DeleteOnClose, False)
    qtbot.addWidget(overlay)
    return overlay


def test_edge_hit_testing(qtbot):
    overlay = _overlay(qtbot)  # local selection 50..110 x 40..90
    assert overlay._edge_at(QPointF(50, 65)) == "left"
    assert overlay._edge_at(QPointF(110, 65)) == "right"
    assert overlay._edge_at(QPointF(80, 40)) == "top"
    assert overlay._edge_at(QPointF(80, 90)) == "bottom"
    assert overlay._edge_at(QPointF(80, 65)) is None  # interior
    # Level with the box but well past its corner is not "on" the edge.
    assert overlay._edge_at(QPointF(50, 140)) is None


def test_move_edge_follows_pointer_displacement(qtbot):
    overlay = _overlay(qtbot)
    overlay._drag_edge = "right"
    overlay._drag_anchor = (QPointF(110, 65), overlay._sel)
    overlay._move_edge(QPointF(140, 65))  # +30 px
    assert overlay._sel.right() == 140  # 110 + 30
    assert overlay._sel.left() == 50  # opposite edge unmoved


def test_move_edge_clamps_to_desktop_and_min_size(qtbot):
    overlay = _overlay(qtbot)
    overlay._drag_edge = "right"
    overlay._drag_anchor = (QPointF(110, 65), overlay._sel)
    overlay._move_edge(QPointF(9999, 65))  # far past the right edge
    assert overlay._sel.right() == 200  # clamped to the desktop width

    overlay2 = _overlay(qtbot)
    overlay2._drag_edge = "left"
    overlay2._drag_anchor = (QPointF(50, 65), overlay2._sel)
    overlay2._move_edge(QPointF(9999, 65))  # left edge dragged past the right
    assert overlay2._sel.width() == 2  # _MIN_SIZE, never collapses


def test_commit_emits_selection_in_global_points(qtbot):
    # Desktop offset by (100, 50); selection sits inside it at global (150, 90).
    overlay = _overlay(qtbot, selection=QRect(150, 90, 60, 50), geometry=QRect(100, 50, 200, 150))
    out = []
    overlay.committed.connect(out.append)
    overlay._sel.setRight(overlay._sel.right() + 10)  # local 110 -> 120: 70 wide
    overlay._commit()
    assert out == [QRect(150, 90, 70, 50)]  # local (50, 40, 70, 50) + origin (100, 50)


def test_cancel_emits_cancelled(qtbot):
    overlay = _overlay(qtbot)
    out = []
    overlay.cancelled.connect(lambda: out.append("cancelled"))
    overlay._cancel()
    assert out == ["cancelled"]


def test_commit_and_cancel_are_one_shot(qtbot):
    overlay = _overlay(qtbot)
    committed, cancelled = [], []
    overlay.committed.connect(committed.append)
    overlay.cancelled.connect(lambda: cancelled.append(1))
    overlay._commit()
    overlay._cancel()  # the overlay already closed; must not fire a second outcome
    assert len(committed) == 1
    assert cancelled == []


def test_refresh_coalesces_repaints(qtbot):
    # A burst of pointer moves must schedule a single repaint, not one per
    # event — repainting the whole dimmed desktop per raw mouse event froze the
    # overlay on macOS. The first _refresh arms a timer; the rest are no-ops.
    overlay = _overlay(qtbot)
    overlay._refresh()
    assert overlay._repaint_pending is True
    overlay._refresh()  # still pending — no second schedule
    assert overlay._repaint_pending is True
    overlay._flush_repaint()  # the timer firing
    assert overlay._repaint_pending is False


def test_release_after_dragging_an_edge_commits(qtbot):
    # Letting go of a dragged edge applies the new crop immediately — no Enter.
    overlay = _overlay(qtbot)  # local selection 50,40,60,50; geometry at origin
    out = []
    overlay.committed.connect(out.append)
    overlay._press(QPointF(110, 65), Qt.LeftButton)  # grab the right edge
    overlay._pointer_moved(QPointF(140, 65))  # drag it +30
    overlay._release(QPointF(140, 65), Qt.LeftButton)
    assert out == [QRect(50, 40, 90, 50)]  # right edge 110 -> 140, width 60 -> 90


def test_release_without_a_drag_does_not_commit(qtbot):
    # A press that didn't land on an edge starts no drag, so release is inert
    # (Esc still cancels); only a real edge drag applies.
    overlay = _overlay(qtbot)
    out = []
    overlay.committed.connect(out.append)
    overlay._press(QPointF(80, 65), Qt.LeftButton)  # interior, no edge
    overlay._release(QPointF(80, 65), Qt.LeftButton)
    assert out == []


def test_paint_does_not_crash(qtbot):
    overlay = _overlay(qtbot)
    overlay._cursor = QPointF(110, 65)  # near an edge -> loupe path runs too
    overlay.resize(200, 150)
    overlay.grab()  # forces a paintEvent


# --- brain/view protocol (shared with SmartOverlay's per-screen machinery) ----


def test_brain_input_methods_drag_an_edge(qtbot):
    # The per-screen views drive the brain through these (positions already in
    # virtual-desktop-local coords); the single window delegates to them too.
    overlay = _overlay(qtbot)  # selection 50..110 x 40..90
    overlay._press(QPointF(110, 65), Qt.LeftButton)  # grab the right edge
    assert overlay._drag_edge == "right"
    overlay._pointer_moved(QPointF(140, 65))  # +30 px
    assert overlay._sel.right() == 140
    overlay._release(QPointF(140, 65), Qt.LeftButton)
    assert overlay._drag_edge is None


def test_cursor_shape_reflects_edge(qtbot):
    overlay = _overlay(qtbot)
    assert overlay._cursor_shape(QPointF(110, 65)) == Qt.SizeHorCursor  # right edge
    assert overlay._cursor_shape(QPointF(80, 40)) == Qt.SizeVerCursor  # top edge
    assert overlay._cursor_shape(QPointF(80, 65)) == Qt.ArrowCursor  # interior


def test_double_click_commits(qtbot):
    overlay = _overlay(qtbot)
    out = []
    overlay.committed.connect(out.append)
    overlay._double_click(QPointF(80, 65), Qt.LeftButton)
    assert len(out) == 1


def test_shared_controller_drives_the_overlay_per_screen(qtbot):
    # Reuse proof: the capture overlay's per-screen controller drives this
    # (non-capture) brain too — one window per screen, real cursors, and the
    # overlay's own commit/cancel as the terminal outcomes that hide the views.
    from PySide6.QtGui import QGuiApplication

    from shotquill.ui.smart_overlay import SmartOverlayController

    overlay = _overlay(qtbot)
    controller = SmartOverlayController(
        overlay, hide_cursor=False, terminal_signals=(overlay.committed, overlay.cancelled)
    )
    assert len(controller._views) == len(QGuiApplication.screens())
    assert all(view._hide_cursor is False for view in controller._views)

    assert controller._finished is False
    overlay.committed.emit(QRect(0, 0, 1, 1))  # a terminal outcome
    assert controller._finished is True  # views hidden before the editor opens
