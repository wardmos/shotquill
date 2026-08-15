# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""The unified full-screen "spotlight" editor surface.

One surface that is BOTH the annotation canvas and the crop-adjust surface, so
the user never perceives two modes — adjusting the capture region is just another
kind of editing. It covers the single screen the selection sits on, paints the
frozen desktop dimmed as context, hosts the existing :class:`AnnotationCanvas` as
a positioned child over the lit selection, and draws crop handles around it.

Dragging a handle moves/resizes the canvas *child* and re-crops it — the
top-level window never changes geometry, so there is no macOS window-resize
feedback (the bug class that sank in-window edge resizing). Annotating happens in
the canvas child exactly as in the framed editor; the shared edit core
(:class:`~shotquill.ui.editor_core.EditorCoreMixin`) drives copy/save/OCR/finish
keys/arrow-nudge identically. The first annotation freezes the crop and the
handles vanish.

Spotlight mode only; the framed (titled-window) editor stays in
:mod:`shotquill.ui.editor`.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QKeySequence,
    QPainter,
    QPen,
    QRegion,
    QShortcut,
)
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget

from shotquill.ocr import get_recognizer
from shotquill.ui import macos_window
from shotquill.ui.editor import _BACKDROP_DIM, _BADGE_STYLE
from shotquill.ui.editor_core import (
    _MIN_CROP,
    EditorCoreMixin,
    RegionContext,
    _toolbar_placement,
)
from shotquill.ui.geometry import crop_edge_hits, loupe_anchor, resize_selection, scale_rect_edges
from shotquill.ui.loupe import LOUPE_H, LOUPE_LABEL_H, LOUPE_OFFSET, LOUPE_W, paint_loupe
from shotquill.ui.smart_overlay import _ACCENT, _HANDLE_GRAB, _HANDLE_SIZE

if TYPE_CHECKING:
    from shotquill.config import Config

_TOOLBAR_GAP = 8  # logical points between the selection and the floating toolbar
_DIRTY_PAD = 8.0


class _DimScreen(QWidget):
    """A bare dim layer over one *other* screen, so the whole desktop darkens
    around the spotlight — not just the selection's screen. Inert: it never takes
    focus and only paints the dim (the surface itself covers the selection's
    screen). Tracks the surface's activation so it doesn't darken whatever the
    user switches to."""

    def __init__(self, geometry: QRect) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.Window
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setGeometry(geometry)

    def paintEvent(self, event) -> None:
        QPainter(self).fillRect(self.rect(), _BACKDROP_DIM)


class SpotlightSurface(EditorCoreMixin, QWidget):
    #: Same contract as EditorWindow: the annotated image + the capture's
    #: on-screen rect, so a pin can size itself for the right screen.
    pin_requested = Signal(QImage, object)
    #: Internal: OCR finished on its worker thread — (lines, error).
    _ocr_done = Signal(object, object)

    def __init__(
        self,
        image: QImage,
        config: Config,
        origin: QRect | None = None,
        region: RegionContext | None = None,
    ) -> None:
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("")
        self._placed = False
        self._drag: tuple[bool, bool, bool, bool] | None = None
        self._live_sel: QRectF | None = None  # selection (surface-local) while dragging
        # Pointer (surface-local) while dragging; drives the loupe.
        self._cursor: QPointF | None = None

        # Cover the screen the selection sits on with one window (no per-screen
        # mirroring of the interactive canvas needed); the other screens each get
        # a bare dim layer (see _dim_screens below).
        screen = (QGuiApplication.screenAt(origin.center()) if origin else None) or (
            QGuiApplication.primaryScreen()
        )
        self._screen_geo = screen.geometry()
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setGeometry(self._screen_geo)
        self.setMouseTracking(True)

        toolbar = self._init_editor_core(
            image, config, origin, region, get_recognizer(), split_outputs=True
        )

        # The lit selection IS the canvas, parented as a positioned child (placed
        # in showEvent / on every crop change), not a central widget. Drop the
        # view's 1px frame so the viewport is exactly the selection rect — the
        # shot then aligns pixel-for-pixel with the lit area (the framed editor
        # gets this for free by sizing the window around the viewport instead).
        self._canvas.setParent(self)
        self._canvas.setFrameShape(QFrame.NoFrame)
        # Frameless means no title bar to carry OCR status; a badge over the
        # canvas shows it instead (see _set_status).
        self._status_badge = QLabel(self._canvas.viewport())
        self._status_badge.setStyleSheet(_BADGE_STYLE)
        self._status_badge.hide()
        # Keep one continuous floating row: annotation tools take the flexible
        # leading section, while copy/save occupy a fixed trailing section. This
        # preserves their order and lets only the annotation section fold.
        self._toolbar = toolbar
        outputs = toolbar.outputs_toolbar
        self._toolbar_row = QWidget(self)
        toolbar_layout = QHBoxLayout(self._toolbar_row)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(0)
        toolbar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        outputs.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        toolbar_layout.addWidget(toolbar, 1)
        toolbar_layout.addWidget(outputs)

        # This screen's slice of the frozen desktop shot, painted dimmed as
        # context (None for non-region captures — then the surface is pure dim).
        self._screen_pixmap = self._build_screen_pixmap()
        # A QImage of that slice for the loupe's colour/coord readout — the shot
        # is frozen, so it is converted once here rather than per paint.
        self._screen_image = (
            self._screen_pixmap.toImage() if self._screen_pixmap is not None else None
        )

        self.reload_finish_keys()
        close_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        close_shortcut.activated.connect(self.close)

        # Intercept crop-edge presses on the canvas before it annotates them,
        # and track no-button hover so the resize cursor can show over an edge
        # (the viewport needs its own tracking flag, not just the view's).
        self._canvas.viewport().setMouseTracking(True)
        self._canvas.viewport().installEventFilter(self)

        # Dim the OTHER screens too (shown/hidden with the surface's activation),
        # so the whole desktop darkens around the spotlight.
        self._dim_screens = [
            _DimScreen(s.geometry())
            for s in QGuiApplication.screens()
            if s.geometry() != self._screen_geo
        ]
        self._wire_adjust_hint()

    # --- screen-local geometry helpers ------------------------------------

    def _to_local(self, rect: QRect) -> QRect:
        """A global rect → surface-local (this screen's origin is the surface's)."""
        return rect.translated(-self._screen_geo.topLeft())

    def _sel_local(self) -> QRectF:
        """The committed selection in surface-local logical points."""
        return QRectF(self._to_local(self._origin))

    def _crop_bounds(self) -> QRectF:
        # Keep keyboard nudges on the screen this surface covers (mouse drags are
        # already bounded to the surface). Pushing the crop onto a neighbouring
        # screen would slide the canvas child out of this window.
        return QRectF(self._region.geometry).intersected(QRectF(self._screen_geo))

    def _build_screen_pixmap(self):
        from PySide6.QtGui import QPixmap

        if self._region is None:
            return None
        geo = self._region.geometry
        local = (
            self._screen_geo.x() - geo.x(),
            self._screen_geo.y() - geo.y(),
            self._screen_geo.width(),
            self._screen_geo.height(),
        )
        phys = QRect(*scale_rect_edges(local, self._region_sx, self._region_sy))
        shot = self._region.screenshot
        return QPixmap.fromImage(shot.copy(phys.intersected(shot.rect())))

    # --- window lifecycle -------------------------------------------------

    def showEvent(self, event) -> None:
        self._escape_guard.enable()
        super().showEvent(event)
        self._cover_menubar()
        self._show_dim_screens()
        if not self._placed:
            self._placed = True
            self._place_canvas()

    def changeEvent(self, event) -> None:
        # macOS re-shows the system menu bar whenever this window (re)activates;
        # push back above it each time so it stays covered (and the crop stays
        # adjustable all the way up to the screen top). The capture overlay is
        # transient so it never hits this; the editor is where the user stays.
        # The other-screen dim layers track activation too, so they don't darken
        # whatever the user switches to.
        if event.type() == QEvent.ActivationChange:
            if self.isActiveWindow():
                # Re-level the native window without moving keyboard focus away
                # from an active graphics text editor in the canvas.
                self._cover_menubar(take_focus=False)
                self._show_dim_screens()
                self._canvas.restore_text_focus()
            else:
                self._hide_dim_screens()
        super().changeEvent(event)

    def _show_dim_screens(self) -> None:
        for dim in self._dim_screens:
            dim.show()
            if sys.platform == "darwin":
                macos_window.raise_above_menubar(dim)

    def _hide_dim_screens(self) -> None:
        for dim in self._dim_screens:
            dim.hide()

    def _cover_menubar(self, *, take_focus: bool = True) -> None:
        # Match the capture overlay's proven sequence: set the resizable style
        # mask FIRST (it must not run after the level change and reset it), then
        # raise the NSWindow above the menu bar, then optionally take focus on
        # initial presentation. Re-activation only needs the native re-leveling.
        macos_window.set_resizable(self, False)
        self.raise_()
        if sys.platform == "darwin":
            macos_window.raise_above_menubar(self)
        self.raise_()
        if take_focus:
            self.activateWindow()
            self.setFocus()

    def closeEvent(self, event) -> None:
        self._escape_guard.disable()
        # Stop a late text focus-out from committing onto the dying undo stack.
        self._canvas.begin_teardown()
        for dim in self._dim_screens:
            dim.close()
            dim.deleteLater()
        self._dim_screens = []
        super().closeEvent(event)

    def keyPressEvent(self, event) -> None:
        if self.handle_key(event):  # arrow crop-nudge + finish keys (shared core)
            return
        super().keyPressEvent(event)

    # --- placement (the shell hook + toolbar/badge) -----------------------

    def place_for_selection(self, origin: QRect) -> None:
        # The surface re-places the canvas CHILD, never the top-level window.
        self._place_canvas()

    def _place_canvas(self) -> None:
        local = self._to_local(self._origin)
        self._canvas.setGeometry(local)
        self._canvas.fitInView(self._canvas.sceneRect(), Qt.KeepAspectRatio)
        self._reposition_toolbar()
        self.update()

    def _reposition_toolbar(self) -> None:
        from PySide6.QtGui import QCursor

        self._toolbar.adjustSize()
        self._toolbar.outputs_toolbar.adjustSize()
        self._toolbar_row.adjustSize()
        preferred = self._toolbar_row.sizeHint()
        # The fixed trailing output section keeps its preferred width; the
        # leading annotation bar absorbs the constraint and exposes overflow.
        self._toolbar_row.resize(min(preferred.width(), self.width()), preferred.height())
        self._toolbar_row.layout().activate()
        sel = self._to_local(self._origin)
        area, align_right = _toolbar_placement(QCursor.pos(), self._origin)
        tb = self._toolbar_row.size()
        x = sel.right() - tb.width() if align_right else sel.left()
        if area == Qt.BottomToolBarArea:
            y = sel.bottom() + _TOOLBAR_GAP
        else:
            y = sel.top() - tb.height() - _TOOLBAR_GAP
        # Clamp inside the surface so the toolbar is always reachable.
        x = min(max(x, 0), max(self.width() - tb.width(), 0))
        y = min(max(y, 0), max(self.height() - tb.height(), 0))
        self._toolbar_row.move(int(x), int(y))
        self._toolbar_row.show()
        self._toolbar_row.raise_()

    # --- painting ---------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        if self._screen_pixmap is not None:
            painter.drawPixmap(self.rect(), self._screen_pixmap)
        painter.fillRect(self.rect(), _BACKDROP_DIM)
        if self._drag is not None and self._live_sel is not None:
            # Canvas is hidden mid-drag; paint the live lit selection ourselves,
            # plus the running size readout and a pixel loupe at the dragged edge
            # so the crop boundary can be placed exactly.
            self._paint_lit_slice(painter, self._live_sel)
            self._paint_handles(painter, self._live_sel)
            self._paint_size_label(painter, self._live_sel)
            self._paint_loupe(painter)
        elif self.crop_adjustable():
            self._paint_handles(painter, self._sel_local())

    def _paint_lit_slice(self, painter: QPainter, sel: QRectF) -> None:
        if self._screen_pixmap is None:
            painter.fillRect(sel, QColor("black"))
            return
        source = QRectF(
            sel.x() * self._region_sx,
            sel.y() * self._region_sy,
            sel.width() * self._region_sx,
            sel.height() * self._region_sy,
        )
        painter.drawPixmap(sel, self._screen_pixmap, source)

    def _paint_handles(self, painter: QPainter, sel: QRectF) -> None:
        # Outline + the eight grab handles, both drawn just OUTSIDE the lit
        # selection so the canvas child (which fills the selection exactly) can't
        # cover them — the handles read fully, even at rest.
        painter.setPen(QPen(_ACCENT, 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(sel.adjusted(-1, -1, 1, 1))
        painter.setPen(QPen(_ACCENT, 1))
        painter.setBrush(QColor("white"))
        for rect in self._handle_rects(sel):
            painter.drawRect(rect)

    @staticmethod
    def _handle_rects(sel: QRectF) -> list[QRectF]:
        """The eight grab-handle squares, each sitting just outside the matching
        edge/corner of ``sel`` (so the canvas child can't hide it)."""
        cx, cy = sel.center().x(), sel.center().y()
        half = _HANDLE_SIZE / 2
        rects = []
        for ax in (sel.left(), cx, sel.right()):
            for ay in (sel.top(), cy, sel.bottom()):
                if ax == cx and ay == cy:
                    continue
                dx = -1 if ax < cx else (1 if ax > cx else 0)
                dy = -1 if ay < cy else (1 if ay > cy else 0)
                hx, hy = ax + dx * half, ay + dy * half
                rects.append(QRectF(hx - half, hy - half, _HANDLE_SIZE, _HANDLE_SIZE))
        return rects

    def _size_text(self, sel: QRectF) -> str:
        """The selection size in native screenshot pixels, e.g. ``240 × 120``."""
        w = int(round(sel.width() * self._region_sx))
        h = int(round(sel.height() * self._region_sy))
        return f"{w} × {h}"

    def _paint_size_label(self, painter: QPainter, sel: QRectF) -> None:
        if self._region is None:
            return
        label = self._size_text(sel)
        font = QFont(self.font())
        font.setPointSize(12)
        painter.setFont(font)
        width = painter.fontMetrics().horizontalAdvance(label) + 12
        box = QRect(int(sel.x()), max(int(sel.y()) - 24, 2), width, 20)
        painter.fillRect(box, QColor(0, 0, 0, 160))
        painter.setPen(Qt.white)
        painter.drawText(box, Qt.AlignCenter, label)

    def _paint_loupe(self, painter: QPainter) -> None:
        # Same magnifier the capture overlay uses, sampling this screen's frozen
        # slice; only shown mid-drag (the canvas child hides it otherwise).
        if self._cursor is None or self._screen_pixmap is None:
            return
        paint_loupe(
            painter,
            pixmap=self._screen_pixmap,
            image=self._screen_image,
            cursor=self._cursor,
            sx=self._region_sx,
            sy=self._region_sy,
            bound_w=self.width(),
            bound_h=self.height(),
            accent=_ACCENT,
            font=self.font(),
        )

    @staticmethod
    def _region_from_rect(rect: QRectF | QRect, *, pad: float = _DIRTY_PAD) -> QRegion:
        qrect = QRectF(rect).adjusted(-pad, -pad, pad, pad).toAlignedRect()
        return QRegion(qrect) if not qrect.isEmpty() else QRegion()

    @staticmethod
    def _union_regions(*regions: QRegion | None) -> QRegion | None:
        out = QRegion()
        for region in regions:
            if region is None or region.isEmpty():
                continue
            out = out.united(region)
        return out if not out.isEmpty() else None

    def _selection_dirty_region(self, sel: QRectF | None) -> QRegion | None:
        if sel is None:
            return None
        return self._region_from_rect(sel.adjusted(0, -28, 0, 0))

    def _loupe_dirty_region(self, cursor: QPointF | None) -> QRegion | None:
        if cursor is None:
            return None
        ax, ay = loupe_anchor(
            cursor.x(),
            cursor.y(),
            LOUPE_W,
            LOUPE_H + LOUPE_LABEL_H,
            self.width(),
            self.height(),
            LOUPE_OFFSET,
        )
        return self._region_from_rect(QRectF(ax, ay, LOUPE_W, LOUPE_H + LOUPE_LABEL_H))

    def _update_drag_region(
        self,
        old_sel: QRectF | None,
        new_sel: QRectF | None,
        old_cursor: QPointF | None,
        new_cursor: QPointF | None,
    ) -> None:
        dirty = self._union_regions(
            self._selection_dirty_region(old_sel),
            self._selection_dirty_region(new_sel),
            self._loupe_dirty_region(old_cursor),
            self._loupe_dirty_region(new_cursor),
        )
        if dirty is None:
            return
        clipped = dirty.intersected(QRegion(self.rect()))
        if not clipped.isEmpty():
            self.update(clipped)

    # --- mouse: handle drags (eventFilter on the canvas + bare-area presses) ---

    def eventFilter(self, obj, event) -> bool:
        if obj is self._canvas.viewport():
            etype = event.type()
            if etype == QEvent.Resize:
                # Re-fit once the viewport has actually resized. Calling
                # fitInView straight after setGeometry can use the stale
                # (pre-resize) viewport size and leave the shot scaled-down and
                # letterboxed (grey margins inside the selection) — and only
                # sometimes, depending on when the resize lands.
                self._canvas.fitInView(self._canvas.sceneRect(), Qt.KeepAspectRatio)
            elif etype == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                local = self._canvas.viewport().mapTo(self, event.position().toPoint())
                if self._try_begin_handle_drag(QPointF(local)):
                    return True  # consumed: a crop resize, not an annotation
            elif etype == QEvent.MouseMove and not event.buttons():
                local = self._canvas.viewport().mapTo(self, event.position().toPoint())
                if self._update_hover_cursor(QPointF(local)):
                    # Over a handle: consume the move so the QGraphicsView doesn't
                    # reset the resize cursor back to the default on this hover.
                    return True
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event) -> None:
        # A press on the surface's bare area (just outside the canvas) can still
        # grab a handle whose band straddles the selection edge.
        if event.button() == Qt.LeftButton and self._try_begin_handle_drag(event.position()):
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag is not None:
            self._update_handle_drag(event.position())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is not None and event.button() == Qt.LeftButton:
            self._end_handle_drag(event.position())
            return
        super().mouseReleaseEvent(event)

    def _edges_at(self, surface_pos: QPointF) -> tuple[bool, bool, bool, bool]:
        sel = self._sel_local()
        return crop_edge_hits(
            surface_pos.x() - sel.x(),
            surface_pos.y() - sel.y(),
            sel.width(),
            sel.height(),
            _HANDLE_GRAB,
        )

    def _try_begin_handle_drag(self, surface_pos: QPointF) -> bool:
        if not self.crop_adjustable():
            return False
        edges = self._edges_at(surface_pos)
        if not any(edges):
            return False
        self._drag = edges
        self._live_sel = self._sel_local()
        self._canvas.hide()  # paint the live lit slice ourselves while dragging
        self.grabMouse()
        self._update_drag_region(None, self._live_sel, None, self._cursor)
        return True

    def _update_handle_drag(self, surface_pos: QPointF) -> None:
        old_sel = QRectF(self._live_sel) if self._live_sel is not None else None
        old_cursor = QPointF(self._cursor) if self._cursor is not None else None
        sel = self._sel_local()  # committed base; active edge tracks the pointer
        bounds = (0.0, 0.0, float(self.width()), float(self.height()))
        new = resize_selection(
            (sel.x(), sel.y(), sel.width(), sel.height()),
            self._drag,
            surface_pos.x(),
            surface_pos.y(),
            bounds,
            _MIN_CROP,
        )
        self._live_sel = QRectF(*new)
        self._cursor = QPointF(surface_pos)  # the loupe magnifies around the dragged edge
        self._update_drag_region(old_sel, self._live_sel, old_cursor, self._cursor)

    def _end_handle_drag(self, surface_pos: QPointF) -> None:
        self._update_handle_drag(surface_pos)
        if self.mouseGrabber() is self:
            self.releaseMouse()
        self._drag = None
        self._cursor = None  # drag over: the loupe disappears with it
        # Commit: surface-local selection → global, then re-crop + re-place.
        self._selection = QRectF(self._live_sel).translated(self._screen_geo.topLeft())
        self._live_sel = None
        self.recrop_selection()
        self._place_canvas()
        self._canvas.show()
        self.update()

    def _update_hover_cursor(self, surface_pos: QPointF) -> bool:
        """Show a resize cursor over a crop edge; return True when one is set."""
        cursor = _crop_cursor(self._edges_at(surface_pos)) if self.crop_adjustable() else None
        viewport = self._canvas.viewport()
        if cursor is None:
            viewport.unsetCursor()
            return False
        viewport.setCursor(cursor)
        return True


def _crop_cursor(edges: tuple[bool, bool, bool, bool]):
    """The resize cursor for the grabbed ``edges``, or None when none are."""
    left, top, right, bottom = edges
    if (left and top) or (right and bottom):
        return Qt.SizeFDiagCursor
    if (right and top) or (left and bottom):
        return Qt.SizeBDiagCursor
    if left or right:
        return Qt.SizeHorCursor
    if top or bottom:
        return Qt.SizeVerCursor
    return None
