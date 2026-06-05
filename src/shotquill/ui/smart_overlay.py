# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Full-screen smart-capture overlay.

Shows a frozen, dimmed screenshot of the whole virtual desktop and picks the
capture mode from what the pointer does — no separate region/window hotkeys:

* Hovering an application window lights it up; a click captures that window.
* Hovering empty space lights the whole desktop; a click captures full screen.
* Pressing and dragging draws a rectangle and captures that region on release.

Esc or a right-click cancels. This folds the old ``RegionOverlay`` and
``WindowPicker`` into one interaction.

Hovered windows show a *live preview*: the frozen desktop screenshot renders
windows as they were stacked, so a partially covered window would preview with
its occluder baked in. Instead the overlay asks ``window_preview`` (a callable
mapping a window id to that window's own un-occluded pixels, which the window
server keeps even for covered windows) and composites the result over the
window's bounds. Moving the pointer away simply falls back to the frozen
screenshot — the real desktop's stacking order is never touched, so there is
nothing to restore. Region and full-screen modes are unaffected.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from shotquill.i18n import t
from shotquill.ui.geometry import (
    loupe_anchor,
    scale_rect,
    scale_rect_edges,
    selection_rect,
    window_at_point,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from shotquill.capture.base import WindowInfo

_DIM = QColor(0, 0, 0, 120)
_ACCENT = QColor("#2d7ff9")
_MIN_SIZE = 2
# How far the pointer must travel after a press before we treat it as a region
# drag rather than a click on the hovered window / full screen.
_DRAG_THRESHOLD = 4
# Pixel loupe that follows the pointer so region edges can be placed precisely.
_LOUPE_W = 120  # loupe display size, logical points
_LOUPE_H = 90
_LOUPE_ZOOM = 4  # one native pixel becomes a 4x4-point block inside the loupe
_LOUPE_OFFSET = 20  # gap between the pointer and the loupe
_LOUPE_LABEL_H = 20  # readout strip under the magnified pixels
# How long the pointer must rest on a window before its un-occluded preview is
# fetched — sweeping across windows must not fire a capture per window passed.
_PREVIEW_DELAY_MS = 120


class SmartOverlay(QWidget):
    #: Capture signals also carry the shot's on-screen rect (global, logical
    #: points) so the editor can open right where the shot was taken.
    region_selected = Signal(QImage, QRect)
    window_selected = Signal(int, QRect)
    fullscreen_selected = Signal()
    cancelled = Signal()
    #: Internal: a fetched window preview (null QImage = fetch failed). Emitted
    #: from the fetch thread; the queued delivery hops back to the GUI thread.
    _preview_ready = Signal(int, QImage)

    def __init__(
        self,
        screenshot: QImage,
        geometry: QRect,
        windows: list[WindowInfo],
        window_preview: Callable[[int], QImage | None] | None = None,
    ) -> None:
        super().__init__()
        self._screenshot = screenshot
        self._pixmap = QPixmap.fromImage(screenshot)
        self._geometry = geometry
        self._windows = windows
        # Window bounds are global; the overlay's coordinates are relative to the
        # virtual desktop origin, so shift them once for hit-testing and drawing.
        self._boxes = [
            (w.bounds.x - geometry.x(), w.bounds.y - geometry.y(), w.bounds.width, w.bounds.height)
            for w in windows
        ]
        # Ratio between native screenshot pixels and logical overlay points.
        self._sx = screenshot.width() / max(geometry.width(), 1)
        self._sy = screenshot.height() / max(geometry.height(), 1)

        self._hover: int | None = None  # window under the pointer, or None for full screen
        self._cursor: QPointF | None = None  # last pointer position, drives the loupe
        self._origin = None
        self._current = None
        self._dragging = False
        self._press_hover: int | None = None
        self._activated = False

        # Un-occluded per-window previews: window id -> pixmap, or None when the
        # fetch failed (so it is not retried and painting falls back to the
        # frozen screenshot). Fetches run on a thread — a wedged capture service
        # must not freeze a screen-covering overlay — after a short hover rest.
        # At most one fetch is in flight: sweeping the pointer across many
        # windows must not pile worker threads onto the window server. When a
        # fetch lands and the hover has moved on, the ready handler re-arms.
        self._window_preview = window_preview
        self._previews: dict[int, QPixmap | None] = {}
        self._preview_busy = False
        self._closed = False
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(_PREVIEW_DELAY_MS)
        self._preview_timer.timeout.connect(self._request_preview)
        self._preview_ready.connect(self._on_preview_ready)

        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.setGeometry(geometry)

    def changeEvent(self, event) -> None:
        # If something steals focus while the overlay is up — a hot corner firing
        # Mission Control / App Exposé, Cmd-Tab, a click elsewhere — cancel
        # instead of leaving a dimmed, screen-covering window the user can't
        # dismiss (Esc only works while we hold keyboard focus).
        if event.type() == QEvent.ActivationChange:
            if self.isActiveWindow():
                self._activated = True
            elif self._activated:
                self._cancel()
        super().changeEvent(event)

    # --- painting ---------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), self._pixmap)
        painter.fillRect(self.rect(), _DIM)

        if self._dragging and self._origin is not None and self._current is not None:
            self._paint_region(painter)
        elif self._hover is not None:
            self._paint_window(painter)
        else:
            self._paint_fullscreen(painter)

        if self._cursor is not None:
            self._paint_loupe(painter)

    def _paint_region(self, painter: QPainter) -> None:
        sel = self._selection()
        source = QRectF(
            *scale_rect((sel.x(), sel.y(), sel.width(), sel.height()), self._sx, self._sy)
        )
        painter.drawPixmap(QRectF(sel), self._pixmap, source)
        painter.setPen(QPen(_ACCENT, 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(sel)
        self._draw_size_label(painter, sel, source)

    def _paint_window(self, painter: QPainter) -> None:
        bx, by, bw, bh = self._boxes[self._hover]
        sel = QRect(int(bx), int(by), int(bw), int(bh))
        source = QRectF(*scale_rect((bx, by, bw, bh), self._sx, self._sy))
        # Restore the hovered window to full brightness, then outline it. When
        # its un-occluded preview has arrived, draw that instead so windows
        # covered by others at trigger time still preview in full.
        preview = self._previews.get(self._windows[self._hover].window_id)
        if preview is not None:
            painter.drawPixmap(QRectF(sel), preview, QRectF(preview.rect()))
        else:
            painter.drawPixmap(QRectF(sel), self._pixmap, source)
        painter.setPen(QPen(_ACCENT, 3))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(sel)
        self._draw_window_label(painter, sel, self._windows[self._hover])

    def _paint_fullscreen(self, painter: QPainter) -> None:
        # Pointer is over empty space: restore the whole desktop to full
        # brightness and outline it so a click clearly means "full screen".
        rect = self.rect()
        painter.drawPixmap(rect, self._pixmap)
        painter.setPen(QPen(_ACCENT, 3))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect.adjusted(1, 1, -2, -2))
        self._draw_hint(painter)

    def _draw_size_label(self, painter: QPainter, sel: QRect, source: QRectF) -> None:
        label = f"{int(source.width())} × {int(source.height())}"
        painter.setFont(QFont("", 12))
        text_w = painter.fontMetrics().horizontalAdvance(label) + 12
        box = QRect(sel.x(), max(sel.y() - 24, 2), text_w, 20)
        painter.fillRect(box, QColor(0, 0, 0, 160))
        painter.setPen(Qt.white)
        painter.drawText(box, Qt.AlignCenter, label)

    def _draw_window_label(self, painter: QPainter, sel: QRect, window: WindowInfo) -> None:
        text = window.owner
        if window.title:
            text = f"{window.owner} · {window.title}"
        painter.setFont(QFont("", 12))
        text_w = painter.fontMetrics().horizontalAdvance(text) + 16
        box = QRect(sel.x(), max(sel.y() - 26, 2), min(text_w, sel.width() or text_w), 22)
        painter.fillRect(box, QColor(0, 0, 0, 180))
        painter.setPen(Qt.white)
        painter.drawText(box.adjusted(8, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft, text)

    def _paint_loupe(self, painter: QPainter) -> None:
        cx, cy = self._cursor.x(), self._cursor.y()
        # Native pixel under the pointer (clamped so edge hovering stays valid).
        px = min(max(int(cx * self._sx), 0), self._screenshot.width() - 1)
        py = min(max(int(cy * self._sy), 0), self._screenshot.height() - 1)
        ax, ay = loupe_anchor(
            cx, cy, _LOUPE_W, _LOUPE_H + _LOUPE_LABEL_H, self.width(), self.height(), _LOUPE_OFFSET
        )
        view = QRectF(ax, ay, _LOUPE_W, _LOUPE_H)

        # A native-resolution patch centred on the pointer, blown up without
        # smoothing so individual pixels (and thus the exact region boundary)
        # stay visible. Near screen edges the patch is clamped to the
        # screenshot and the remainder left dark.
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

        # Crosshair over the centre pixel, then a frame around the loupe.
        painter.setPen(QPen(_ACCENT, 1))
        center = view.center()
        painter.drawLine(QPointF(view.left(), center.y()), QPointF(view.right(), center.y()))
        painter.drawLine(QPointF(center.x(), view.top()), QPointF(center.x(), view.bottom()))
        painter.setPen(QPen(QColor(255, 255, 255, 200), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(view)

        # Pointer position (native pixels) and the colour under it.
        color = self._screenshot.pixelColor(px, py)
        label = f"({px}, {py})  {color.name().upper()}"
        box = QRectF(ax, ay + _LOUPE_H, _LOUPE_W, _LOUPE_LABEL_H)
        painter.fillRect(box, QColor(0, 0, 0, 200))
        painter.setFont(QFont("", 10))
        painter.setPen(Qt.white)
        painter.drawText(box, Qt.AlignCenter, label)

    def _draw_hint(self, painter: QPainter) -> None:
        hint = t("smart.hint")
        painter.setFont(QFont("", 14))
        metrics = painter.fontMetrics()
        box = QRect(0, 0, metrics.horizontalAdvance(hint) + 32, 40)
        box.moveCenter(self.rect().center())
        painter.fillRect(box, QColor(0, 0, 0, 180))
        painter.setPen(Qt.white)
        painter.drawText(box, Qt.AlignCenter, hint)

    # --- interaction ------------------------------------------------------

    def _selection(self) -> QRect:
        x, y, w, h = selection_rect(
            self._origin.x(), self._origin.y(), self._current.x(), self._current.y()
        )
        return QRect(int(x), int(y), int(w), int(h))

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        self._cursor = pos
        if self._origin is not None:
            self._current = pos
            if not self._dragging:
                dx = pos.x() - self._origin.x()
                dy = pos.y() - self._origin.y()
                if (dx * dx + dy * dy) ** 0.5 > _DRAG_THRESHOLD:
                    self._dragging = True
            self.update()
            return
        hover = window_at_point(self._boxes, pos.x(), pos.y())
        if hover != self._hover:
            self._hover = hover
            self._schedule_preview()
        # The loupe follows every move, so repaint unconditionally.
        self.update()

    # --- un-occluded window previews ---------------------------------------

    def _schedule_preview(self) -> None:
        """(Re)arm the preview fetch for the newly hovered window, if needed."""
        self._preview_timer.stop()
        if self._closed or self._window_preview is None or self._hover is None:
            return
        window_id = self._windows[self._hover].window_id
        if window_id in self._previews:
            return
        self._preview_timer.start()

    def _request_preview(self) -> None:
        if self._closed or self._window_preview is None or self._hover is None:
            return
        window_id = self._windows[self._hover].window_id
        if window_id in self._previews:
            return
        if self._preview_busy:
            return  # single-flight: _on_preview_ready re-arms for the current hover
        self._preview_busy = True
        threading.Thread(
            target=self._fetch_preview, args=(window_id,), daemon=True, name="sq-preview"
        ).start()

    def _fetch_preview(self, window_id: int) -> None:
        """Worker thread: grab one window's pixels and hand them to the GUI thread."""
        try:
            image = self._window_preview(window_id)
        except Exception:
            image = None
        try:
            self._preview_ready.emit(window_id, image if image is not None else QImage())
        except RuntimeError:
            pass  # overlay closed (and deleted) while the fetch was in flight

    def _on_preview_ready(self, window_id: int, image: QImage) -> None:
        self._preview_busy = False
        if self._closed:
            return
        # A null image marks a failed fetch; remember it so it isn't retried and
        # painting keeps using the frozen screenshot for that window.
        self._previews[window_id] = None if image.isNull() else QPixmap.fromImage(image)
        if self._hover is not None and self._windows[self._hover].window_id == window_id:
            self.update()
        else:
            # The pointer moved on while this fetch ran; arm a fetch for the
            # now-hovered window (if any) so it isn't starved by the busy gate.
            self._schedule_preview()

    def closeEvent(self, event) -> None:
        # No new preview fetches once the overlay is going away; an in-flight
        # worker may still finish, but its result is dropped above.
        self._closed = True
        self._preview_timer.stop()
        super().closeEvent(event)

    def leaveEvent(self, event) -> None:
        self._cursor = None
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.RightButton:
            self._cancel()
            return
        if event.button() == Qt.LeftButton:
            self._origin = event.position()
            self._current = event.position()
            self._dragging = False
            self._press_hover = self._hover
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton or self._origin is None:
            return
        self._current = event.position()
        if self._dragging:
            self._accept_region()
        else:
            self._accept_target(self._press_hover)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self._cancel()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self._dragging:
                self._accept_region()
            else:
                self._accept_target(self._hover)

    def _accept_region(self) -> None:
        sel = self._selection()
        if sel.width() < _MIN_SIZE or sel.height() < _MIN_SIZE:
            self._cancel()
            return
        # Crop from the float drag points (not the int-snapped UI rect) and
        # convert by edges, so fractional scale factors never clip the
        # right/bottom pixel row; clamp to the screenshot so QImage.copy
        # can't pad the crop with uninitialized pixels.
        logical = selection_rect(
            self._origin.x(), self._origin.y(), self._current.x(), self._current.y()
        )
        phys = QRect(*scale_rect_edges(logical, self._sx, self._sy))
        cropped = self._screenshot.copy(phys.intersected(self._screenshot.rect()))
        # Overlay coordinates are relative to the virtual-desktop origin; shift
        # back so the emitted rect is in global screen coordinates.
        self.region_selected.emit(cropped, sel.translated(self._geometry.topLeft()))
        self.close()

    def _accept_target(self, hover: int | None) -> None:
        if hover is not None:
            window = self._windows[hover]
            bounds = QRect(
                int(window.bounds.x),
                int(window.bounds.y),
                int(window.bounds.width),
                int(window.bounds.height),
            )
            self.window_selected.emit(window.window_id, bounds)
        else:
            self.fullscreen_selected.emit()
        self.close()

    def _cancel(self) -> None:
        self.cancelled.emit()
        self.close()
