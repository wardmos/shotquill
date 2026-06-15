# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""A full-desktop overlay for re-adjusting a region capture's crop.

The editor opens this when the user grabs one of the screenshot's edges to
resize the crop. Re-cropping inside the editor's own window is fragile — the
window is sized and placed to sit exactly over the shot, so growing the crop
means resizing/moving that window under the pointer (jittery on a real window
manager) or letterboxing the shot inside it (blank margins). Instead this
overlay covers the whole virtual desktop, paints the frozen full-desktop
screenshot dimmed, brightens the current selection, and lets the user drag a
single edge against the live context — exactly like the capture overlay's
region selection, with the same pixel loupe. On confirm it hands the new
selection (global, logical points) back to the editor, which re-crops once.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

from shotquill.i18n import t
from shotquill.ui.geometry import scale_rect

# Mirror the capture overlay's look so adjusting reads as the same tool.
_DIM = QColor(0, 0, 0, 120)
_ACCENT = QColor("#2d7ff9")
_MIN_SIZE = 2  # logical points; matches the overlay's minimum selection
_EDGE_GRAB = 10  # how close (logical points) the pointer must be to grab an edge

_LOUPE_W = 120  # loupe display size, logical points
_LOUPE_H = 90
_LOUPE_ZOOM = 4  # one native pixel becomes a 4x4-point block inside the loupe
_LOUPE_OFFSET = 20  # gap between the pointer and the loupe
_LOUPE_LABEL_H = 20  # readout strip under the magnified pixels
# Coalesce repaints to ~60fps. Every pointer move repaints the whole dimmed
# desktop (a Retina-sized blit) plus the loupe; doing that per raw mouse event
# (100+/s) backed the GUI thread up on macOS until the overlay froze grey. The
# move math still runs per event — only the repaint is throttled.
_REPAINT_MS = 16


def _prefers_fullscreen() -> bool:
    """Wayland owns geometry/stacking, so ask for fullscreen there (see present)."""
    return QGuiApplication.platformName().lower().startswith("wayland")


class RegionAdjustOverlay(QWidget):
    """Drag one edge of an existing selection over the full-desktop screenshot."""

    #: The accepted selection, in global logical points (ready to re-crop from).
    committed = Signal(QRect)
    #: The user dismissed without accepting (Esc, or focus stolen).
    cancelled = Signal()
    #: Internal: state changed and a repaint is due. Nothing listens on the
    #: single-window path; the multi-screen controller repaints every per-screen
    #: view from it (see SmartOverlayController). Mirrors SmartOverlay.changed.
    changed = Signal()

    def __init__(
        self,
        screenshot: QImage,
        geometry: QRect,
        selection: QRect,
        edge: str | None = None,
    ) -> None:
        super().__init__()
        self._screenshot = screenshot  # full-desktop shot, native pixels
        self._pixmap = QPixmap.fromImage(screenshot)
        # The dimmed desktop, pre-composed once: blitting this each frame avoids
        # alpha-filling the whole (Retina-sized) screen on every pointer move,
        # which made dragging stutter. The bright selection is painted on top.
        self._dimmed = QPixmap(self._pixmap)
        _p = QPainter(self._dimmed)
        _p.fillRect(self._dimmed.rect(), _DIM)
        _p.end()
        self._geometry = geometry  # virtual desktop, global logical points
        # Ratio between native screenshot pixels and logical overlay points.
        self._sx = screenshot.width() / max(geometry.width(), 1)
        self._sy = screenshot.height() / max(geometry.height(), 1)
        # The selection lives in overlay-local logical points (relative to the
        # virtual-desktop origin); convert back to global only when emitting.
        self._sel = QRectF(selection.translated(-geometry.topLeft()))

        self._cursor: QPointF | None = None  # last pointer position, drives the loupe
        self._drag_edge: str | None = None  # edge currently being dragged, if any
        self._drag_anchor: tuple[QPointF, QRectF] | None = None
        self._closed = False
        self._activated = False
        self._repaint_pending = False  # a coalesced repaint is scheduled (see _refresh)

        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setMouseTracking(True)
        self.setGeometry(geometry)
        # The edge the user grabbed in the editor only seeds the cursor shape as
        # a hint; the drag itself starts fresh from a press here, so a stray move
        # on the way in can't run an edge away on its own.
        if edge in ("left", "right"):
            self.setCursor(Qt.SizeHorCursor)
        elif edge in ("top", "bottom"):
            self.setCursor(Qt.SizeVerCursor)

    def present(self) -> None:
        """Show the overlay as a single full-desktop, stay-on-top window.

        Deliberately *not* routed through the capture overlay's per-screen
        controller, and on macOS deliberately *neither* ``showFullScreen`` nor a
        raise-above-the-menu-bar window — both of which the capture overlay uses
        but which each bite here:

        * ``showFullScreen`` enters a native fullscreen *space*, whose animate-in
          (black flash, apparent resolution change, menu-bar shuffle) is jarring
          for a transient editing step.
        * Raising the window above the menu bar puts it at a screensaver-level
          that macOS does not composite normally, so the heavy per-drag repaints
          back the window server up until the overlay freezes.

        A plain stay-on-top window (the same path X11 takes) composites normally
        — smooth, no freeze — and covers everything but the menu-bar strip, which
        adjusting a crop never needs. Wayland still needs fullscreen (it ignores
        stay-on-top + geometry).
        """
        if _prefers_fullscreen():
            self.showFullScreen()
        else:
            self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def changeEvent(self, event) -> None:
        # If focus is stolen while the overlay is up, cancel rather than leave a
        # dimmed, screen-covering window the user can't dismiss (Esc only works
        # while we hold focus). The _activated guard keeps the deactivation that
        # accompanies our own close from firing a second outcome.
        if event.type() == QEvent.ActivationChange:
            if self.isActiveWindow():
                self._activated = True
            elif self._activated and not self._closed:
                self._cancel()
        super().changeEvent(event)

    # --- painting ---------------------------------------------------------

    def paintEvent(self, event) -> None:
        self._paint_all(QPainter(self))

    def _paint_all(self, painter: QPainter) -> None:
        # Painted in virtual-desktop-local coords. A per-screen view calls this
        # with the painter translated by its screen offset so the same code
        # paints each display's slice; the single window calls it untranslated.
        painter.drawPixmap(self.rect(), self._dimmed)  # pre-dimmed (see __init__)
        self._paint_selection(painter)
        near_edge = self._cursor is not None and self._edge_at(self._cursor) is not None
        if self._drag_edge is not None or near_edge:
            self._paint_loupe(painter)
        self._draw_hint(painter)

    def _draw_hint(self, painter: QPainter) -> None:
        hint = t("adjust.overlay_hint")
        painter.setFont(self._label_font(13))
        metrics = painter.fontMetrics()
        box = QRect(0, 0, metrics.horizontalAdvance(hint) + 32, 36)
        box.moveCenter(QPoint(self.rect().center().x(), self.rect().bottom() - 40))
        painter.fillRect(box, QColor(0, 0, 0, 180))
        painter.setPen(Qt.white)
        painter.drawText(box, Qt.AlignCenter, hint)

    def _paint_selection(self, painter: QPainter) -> None:
        sel = self._sel
        rect = (sel.x(), sel.y(), sel.width(), sel.height())
        source = QRectF(*scale_rect(rect, self._sx, self._sy))
        painter.drawPixmap(sel, self._pixmap, source)
        painter.setPen(QPen(_ACCENT, 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(sel)
        # Edge handles: a short bright tick at the middle of each edge so the
        # draggable edges read as draggable.
        painter.setPen(QPen(_ACCENT, 4))
        cx, cy = sel.center().x(), sel.center().y()
        painter.drawLine(QPointF(cx - 10, sel.top()), QPointF(cx + 10, sel.top()))
        painter.drawLine(QPointF(cx - 10, sel.bottom()), QPointF(cx + 10, sel.bottom()))
        painter.drawLine(QPointF(sel.left(), cy - 10), QPointF(sel.left(), cy + 10))
        painter.drawLine(QPointF(sel.right(), cy - 10), QPointF(sel.right(), cy + 10))
        self._draw_size_label(painter, sel, source)

    def _draw_size_label(self, painter: QPainter, sel: QRectF, source: QRectF) -> None:
        label = f"{int(source.width())} × {int(source.height())}"
        painter.setFont(self._label_font(12))
        text_w = painter.fontMetrics().horizontalAdvance(label) + 12
        box = QRect(int(sel.x()), max(int(sel.y()) - 24, 2), text_w, 20)
        painter.fillRect(box, QColor(0, 0, 0, 160))
        painter.setPen(Qt.white)
        painter.drawText(box, Qt.AlignCenter, label)

    def _label_font(self, point_size: int) -> QFont:
        font = QFont(self.font())
        font.setPointSize(point_size)
        return font

    def _paint_loupe(self, painter: QPainter) -> None:
        # A native-resolution patch centred on the pointer, blown up without
        # smoothing so the exact crop boundary stays visible (mirrors the
        # capture overlay's loupe).
        cx, cy = self._cursor.x(), self._cursor.y()
        px = min(max(int(cx * self._sx), 0), self._screenshot.width() - 1)
        py = min(max(int(cy * self._sy), 0), self._screenshot.height() - 1)
        ax, ay = self._loupe_anchor(cx, cy)
        view = QRectF(ax, ay, _LOUPE_W, _LOUPE_H)
        painter.fillRect(view, QColor(0, 0, 0, 220))
        src_w = _LOUPE_W / _LOUPE_ZOOM
        src_h = _LOUPE_H / _LOUPE_ZOOM
        source = QRectF(px + 0.5 - src_w / 2, py + 0.5 - src_h / 2, src_w, src_h)
        clamped = source.intersected(QRectF(0, 0, self._pixmap.width(), self._pixmap.height()))
        if not clamped.isEmpty():
            target = QRectF(
                ax + (clamped.x() - source.x()) * _LOUPE_ZOOM,
                ay + (clamped.y() - source.y()) * _LOUPE_ZOOM,
                clamped.width() * _LOUPE_ZOOM,
                clamped.height() * _LOUPE_ZOOM,
            )
            painter.drawPixmap(target, self._pixmap, clamped)
        painter.setPen(QPen(_ACCENT, 1))
        center = view.center()
        painter.drawLine(QPointF(view.left(), center.y()), QPointF(view.right(), center.y()))
        painter.drawLine(QPointF(center.x(), view.top()), QPointF(center.x(), view.bottom()))
        painter.setPen(QPen(QColor(255, 255, 255, 200), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(view)
        color = self._screenshot.pixelColor(px, py)
        label = f"({px}, {py})  {color.name().upper()}"
        box = QRectF(ax, ay + _LOUPE_H, _LOUPE_W, _LOUPE_LABEL_H)
        painter.fillRect(box, QColor(0, 0, 0, 200))
        painter.setFont(self._label_font(10))
        painter.setPen(Qt.white)
        painter.drawText(box, Qt.AlignCenter, label)

    def _loupe_anchor(self, cx: float, cy: float) -> tuple[float, float]:
        ax = cx + _LOUPE_OFFSET
        if ax + _LOUPE_W > self.width():
            ax = cx - _LOUPE_OFFSET - _LOUPE_W
        ay = cy + _LOUPE_OFFSET
        if ay + _LOUPE_H + _LOUPE_LABEL_H > self.height():
            ay = cy - _LOUPE_OFFSET - _LOUPE_H - _LOUPE_LABEL_H
        return (max(ax, 0.0), max(ay, 0.0))

    # --- interaction ------------------------------------------------------

    def _edge_at(self, pos: QPointF) -> str | None:
        """Which selection edge (if any) the point is close enough to grab.

        The point must also fall within the edge's own span (plus the grab
        margin), so a point level with the box but far past its corner doesn't
        count as on the edge. At a corner the nearer edge wins.
        """
        r = self._sel
        within_v = r.top() - _EDGE_GRAB <= pos.y() <= r.bottom() + _EDGE_GRAB
        within_h = r.left() - _EDGE_GRAB <= pos.x() <= r.right() + _EDGE_GRAB
        near = []
        if within_v and abs(pos.x() - r.left()) <= _EDGE_GRAB:
            near.append(("left", abs(pos.x() - r.left())))
        if within_v and abs(pos.x() - r.right()) <= _EDGE_GRAB:
            near.append(("right", abs(pos.x() - r.right())))
        if within_h and abs(pos.y() - r.top()) <= _EDGE_GRAB:
            near.append(("top", abs(pos.y() - r.top())))
        if within_h and abs(pos.y() - r.bottom()) <= _EDGE_GRAB:
            near.append(("bottom", abs(pos.y() - r.bottom())))
        if not near:
            return None
        return min(near, key=lambda item: item[1])[0]

    def _cursor_shape(self, pos: QPointF):
        """The resize/arrow cursor for a pointer position (drives the views).

        The per-screen views set their own cursor from this (they receive real
        mouse events); the single window applies it in its own handlers.
        """
        edge = self._drag_edge or self._edge_at(pos)
        if edge in ("left", "right"):
            return Qt.SizeHorCursor
        if edge in ("top", "bottom"):
            return Qt.SizeVerCursor
        return Qt.ArrowCursor

    def _refresh(self) -> None:
        # Coalesce repaints to ~60fps: a burst of pointer moves schedules a
        # single repaint, not one per event (see _REPAINT_MS). The first call
        # arms the timer; later ones in the same window are no-ops.
        if self._repaint_pending or self._closed:
            return
        self._repaint_pending = True
        QTimer.singleShot(_REPAINT_MS, self._flush_repaint)

    def _flush_repaint(self) -> None:
        # Repaint the single window and signal the controller (if any) to repaint
        # every per-screen view — same split as SmartOverlay._refresh.
        self._repaint_pending = False
        if self._closed:
            return  # a pending tick can land after commit/cancel closed us
        self.update()
        self.changed.emit()

    # Brain input methods: positions are in virtual-desktop-local coords. The
    # single window passes its own event positions; a per-screen view translates
    # the event into brain coords first. This is the SmartOverlay brain/view split.

    def _pointer_moved(self, pos: QPointF) -> None:
        self._cursor = pos
        if self._drag_edge is not None:
            if self._drag_anchor is None:
                self._drag_anchor = (pos, QRectF(self._sel))
            self._move_edge(pos)
        self._refresh()

    def _press(self, pos: QPointF, button) -> None:
        if button != Qt.LeftButton:
            return
        edge = self._edge_at(pos)
        if edge is not None:
            self._drag_edge = edge
            self._drag_anchor = (pos, QRectF(self._sel))
            self._cursor = pos
            self._refresh()

    def _release(self, pos: QPointF, button) -> None:
        if button == Qt.LeftButton and self._drag_edge is not None:
            # Letting go of a dragged edge applies the new crop immediately —
            # one edge per visit, no separate confirm key. Esc still cancels.
            self._drag_edge = None
            self._drag_anchor = None
            self._commit()

    def _double_click(self, pos: QPointF, button) -> None:
        if button == Qt.LeftButton:
            self._commit()

    # Single-window event handlers (X11 / single-output Wayland) delegate to the
    # brain methods, then keep the OS cursor in sync (the views do this themselves).

    def mouseMoveEvent(self, event) -> None:
        self._pointer_moved(event.position())
        self.setCursor(self._cursor_shape(event.position()))

    def mousePressEvent(self, event) -> None:
        self._press(event.position(), event.button())
        self.setCursor(self._cursor_shape(event.position()))

    def mouseReleaseEvent(self, event) -> None:
        self._release(event.position(), event.button())
        self.setCursor(self._cursor_shape(event.position()))

    def mouseDoubleClickEvent(self, event) -> None:
        self._double_click(event.position(), event.button())

    def _move_edge(self, pos: QPointF) -> None:
        # Move the grabbed edge by the pointer's displacement from where the
        # drag began (not to its absolute position), so the edge tracks the hand
        # even if the grab started a few pixels off the edge.
        start_pos, start_sel = self._drag_anchor
        sel = QRectF(start_sel)
        dx = pos.x() - start_pos.x()
        dy = pos.y() - start_pos.y()
        if self._drag_edge == "left":
            sel.setLeft(min(max(start_sel.left() + dx, 0.0), sel.right() - _MIN_SIZE))
        elif self._drag_edge == "right":
            x = max(min(start_sel.right() + dx, float(self.width())), sel.left() + _MIN_SIZE)
            sel.setRight(x)
        elif self._drag_edge == "top":
            sel.setTop(min(max(start_sel.top() + dy, 0.0), sel.bottom() - _MIN_SIZE))
        elif self._drag_edge == "bottom":
            sel.setBottom(
                max(min(start_sel.bottom() + dy, float(self.height())), sel.top() + _MIN_SIZE)
            )
        self._sel = sel

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key_Escape:
            self._cancel()
        elif key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self._commit()
        else:
            super().keyPressEvent(event)

    def _selection_global(self) -> QRect:
        return QRect(
            int(round(self._sel.x())) + self._geometry.x(),
            int(round(self._sel.y())) + self._geometry.y(),
            int(round(self._sel.width())),
            int(round(self._sel.height())),
        )

    def _commit(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.committed.emit(self._selection_global())
        self.close()

    def _cancel(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.cancelled.emit()
        self.close()

    def closeEvent(self, event) -> None:
        # The editor tears the overlay down with close() (not _commit/_cancel) on
        # its own teardown; mark it closed so the controller's deferred focus
        # check short-circuits instead of cancelling a window that is going away.
        self._closed = True
        super().closeEvent(event)
