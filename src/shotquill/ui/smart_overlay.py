# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Full-screen smart-capture overlay.

Shows a frozen, dimmed screenshot of the whole virtual desktop and picks the
capture mode from what the pointer does — no separate region/window hotkeys:

* Hovering an application window lights it up; a click captures that window.
* Hovering empty space lights the whole desktop; a click captures full screen.
* Pressing and dragging draws a rectangle and selects that region.

While the overlay is up, the arrow keys (or WASD) nudge the pointer one point
per press (Shift: ten) — the loupe magnifies, but the hand still has to hit the
exact pixel, so the keyboard takes over the last few points before and during a
drag. The OS cursor is hidden and a crosshair is painted at the pointer state,
so the marker tracks the keys even on platforms that won't let an app warp the
real pointer (Wayland; macOS without Accessibility).

Releasing a region drag captures immediately and hands off to the editor,
which opens in place over the selection; hand-drawn edges are rarely
pixel-accurate, so the *editor* keeps the selection adjustable with the
arrow keys until the first annotation lands (see ``EditorWindow``). There
used to be a separate pinned-adjustment step here between release and the
editor; folding it into the editor removed one mode from the flow.

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

import sys
import threading
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QGuiApplication,
    QImage,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

from shotquill.config import DEFAULT_HOVER_SWITCH_DELAY_MS, HOVER_SWITCH_NEVER
from shotquill.i18n import t
from shotquill.ui.geometry import (
    loupe_anchor,
    rect_containing,
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
# Keyboard cursor nudging: arrows or WASD move the *real* pointer one logical
# point per press (Shift steps by 10) so a drag can start, follow, and end on
# an exact pixel — the loupe magnifies, but the hand still has to hit the
# spot. The OS cursor is warped *and* the move is applied to the overlay's
# own pointer state, so the loupe, hover highlight, and a held drag follow
# even when the warp is swallowed — and it routinely is: Wayland forbids an
# app from warping the pointer outright, and macOS swallows it without the
# Accessibility permission. So the *visible* marker can't be the OS cursor;
# the overlay hides it (BlankCursor) and paints its own crosshair at the
# pointer state, which always tracks the keys (see _nudge_cursor / _paint_cursor).
_CURSOR_NUDGE_COARSE = 10
_CURSOR_DELTAS = {
    Qt.Key_Left: (-1, 0),
    Qt.Key_Right: (1, 0),
    Qt.Key_Up: (0, -1),
    Qt.Key_Down: (0, 1),
    Qt.Key_A: (-1, 0),
    Qt.Key_D: (1, 0),
    Qt.Key_W: (0, -1),
    Qt.Key_S: (0, 1),
}
# Only Shift (coarse step) and the keypad flag may accompany a nudge key:
# app shortcuts like ⌘A / ⌘W land on the same keys and must not move the
# pointer while the overlay is up.
_NUDGE_MODIFIERS = Qt.ShiftModifier | Qt.KeypadModifier


def _compositor_prefers_fullscreen() -> bool:
    """True when the running Qt platform is Wayland, where the overlay must be
    presented fullscreen rather than as a stay-on-top top-level.

    A Wayland compositor ignores both an app-set window position and
    ``WindowStaysOnTopHint``: a plain frameless top-level is stacked and tiled
    like any other window, so the dim layer can end up *under* foreground
    windows and off-origin. Fullscreen is the compositor-blessed way to own the
    whole output and sit above normal windows, which is exactly the overlay's
    job. Keyed off the live platform name (``wayland`` / ``wayland-egl``), not an
    env var, so the offscreen test platform and X11/macOS keep the geometry path.
    """
    return QGuiApplication.platformName().lower().startswith("wayland")


class SmartOverlay(QWidget):
    #: Capture signals also carry the shot's on-screen rect (global, logical
    #: points) so the editor can open right where the shot was taken.
    region_selected = Signal(QImage, QRect)
    window_selected = Signal(int, QRect)
    fullscreen_selected = Signal()
    cancelled = Signal()
    #: Internal: emitted whenever the overlay's state changes and a repaint is
    #: due. On the single-window path nothing listens (the widget repaints
    #: itself); the multi-screen controller connects it to repaint every
    #: per-screen view that mirrors this shared brain.
    changed = Signal()
    #: Internal: a fetched window preview (null QImage = fetch failed). Emitted
    #: from the fetch thread; the queued delivery hops back to the GUI thread.
    _preview_ready = Signal(int, QImage)

    def __init__(
        self,
        screenshot: QImage,
        geometry: QRect,
        windows: list[WindowInfo],
        window_preview: Callable[[int], QImage | None] | None = None,
        hover_switch_delay_ms: int = DEFAULT_HOVER_SWITCH_DELAY_MS,
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
        # Per-monitor rects in overlay-local coords, used to clip the full-span
        # crosshair guide lines to whichever screen the pointer is on (their
        # geometry is in the same logical points as the overlay; shift to local).
        self._monitors = [
            (
                s.geometry().x() - geometry.x(),
                s.geometry().y() - geometry.y(),
                s.geometry().width(),
                s.geometry().height(),
            )
            for s in QGuiApplication.screens()
        ]

        self._hover: int | None = None  # window under the pointer, or None for full screen
        # Debounced highlight switching: ``_pending_hover`` tracks the target
        # currently under the pointer; the highlight follows only after the
        # pointer rests on it for ``hover_switch_delay_ms`` (or on a press).
        # Without the rest, sweeping the pointer (say, heading somewhere to
        # start a region drag) would strobe the preview through every window
        # crossed on the way. 0 switches immediately; HOVER_SWITCH_NEVER only
        # ever switches on a press.
        self._hover_switch_delay_ms = hover_switch_delay_ms
        self._pending_hover: int | None = None
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(max(hover_switch_delay_ms, 0))
        self._hover_timer.timeout.connect(self._commit_hover)
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
        # Hide the OS cursor and draw our own crosshair (see _paint_cursor): the
        # keyboard nudge can't move the real pointer on Wayland (or macOS without
        # Accessibility), so a system cursor would desync from the loupe/selection.
        self.setCursor(Qt.BlankCursor)
        self.setMouseTracking(True)
        self.setGeometry(geometry)

    def present(self) -> None:
        """Show the overlay on top of everything and take keyboard focus.

        On X11/macOS the frameless stay-on-top top-level sized to the virtual
        desktop in ``__init__`` is shown as-is. On Wayland the compositor owns
        geometry and stacking, so we ask it for fullscreen instead — its
        fullscreen rule raises the surface above normal windows and gives it the
        whole output, which ``WindowStaysOnTopHint`` + ``setGeometry`` cannot do
        there. Multi-monitor Wayland is best-effort: fullscreen is per-output, so
        the overlay covers the screen it lands on (the compositor's active one)
        rather than the full virtual desktop the X11/macOS path spans.

        The app calls this instead of ``show()`` so the platform branch lives in
        one place; the raise/activate/focus that follow are the same everywhere.
        """
        if _compositor_prefers_fullscreen():
            self.showFullScreen()
        else:
            self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def changeEvent(self, event) -> None:
        # If something steals focus while the overlay is up — a hot corner firing
        # Mission Control / App Exposé, Cmd-Tab, a click elsewhere — cancel
        # instead of leaving a dimmed, screen-covering window the user can't
        # dismiss (Esc only works while we hold keyboard focus).
        if event.type() == QEvent.ActivationChange:
            if self.isActiveWindow():
                self._activated = True
            elif self._activated and not self._closed:
                # _closed gates the accepted/cancelled paths: opening the editor
                # right after an accept deactivates the overlay before close()
                # lands, and that deactivation must not fire a second outcome.
                self._cancel()
        super().changeEvent(event)

    # --- painting ---------------------------------------------------------

    def _refresh(self) -> None:
        # Repaint this widget (the single-window path) and signal the
        # multi-screen controller, if any, to repaint every per-screen view.
        self.update()
        self.changed.emit()

    def paintEvent(self, event) -> None:
        self._paint_all(QPainter(self))

    def _paint_all(self, painter: QPainter) -> None:
        # Drawn relative to the virtual-desktop origin. A per-screen view calls
        # this with the painter translated by its screen offset, so the same
        # code paints each display's slice; the single window calls it untranslated.
        painter.drawPixmap(self.rect(), self._pixmap)
        painter.fillRect(self.rect(), _DIM)

        has_selection = self._origin is not None and self._current is not None
        if self._dragging and has_selection:
            self._paint_region(painter)
        elif self._hover is not None:
            self._paint_window(painter)
            self._paint_pending_window(painter)
        elif self._pending_hover is not None:
            self._paint_pending_window(painter)
        else:
            self._paint_fullscreen(painter)

        if self._cursor is not None:
            self._paint_guides(painter)
            self._paint_cursor(painter)
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

    def _paint_pending_window(self, painter: QPainter) -> None:
        # Instant pointing feedback: the window under the pointer keeps its
        # frozen-screenshot pixels at full brightness against the dimmed
        # desktop, traced with a thin outline — under HOVER_SWITCH_NEVER the
        # only hover cue there is — so it is always clear which window a click
        # would select. Deliberately *not* the full highlight: no un-occluded
        # preview is fetched (nothing appears to jump to the front) and no
        # label is drawn.
        if self._pending_hover is None or self._pending_hover == self._hover:
            return
        bx, by, bw, bh = self._boxes[self._pending_hover]
        sel = QRect(int(bx), int(by), int(bw), int(bh))
        source = QRectF(*scale_rect((bx, by, bw, bh), self._sx, self._sy))
        painter.drawPixmap(QRectF(sel), self._pixmap, source)
        # 2 points: clearly visible on the dimmed desktop yet still a step
        # below the committed highlight's 3-point border.
        painter.setPen(QPen(_ACCENT, 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(sel.adjusted(0, 0, -1, -1))

    def _paint_fullscreen(self, painter: QPainter) -> None:
        # Pointer is over empty space: restore the whole desktop to full
        # brightness and outline it so a click clearly means "full screen".
        rect = self.rect()
        painter.drawPixmap(rect, self._pixmap)
        painter.setPen(QPen(_ACCENT, 3))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect.adjusted(1, 1, -2, -2))
        self._draw_hint(painter)

    def _label_font(self, point_size: int) -> QFont:
        # Start from the widget's resolved UI font, not QFont("", n): an empty
        # family name doesn't mean "the default font" — it sends Qt's matcher
        # off to pick whatever it likes (on some Linux boxes an unrelated
        # Ethiopic face), giving the labels oddly proportioned glyphs and
        # uneven spacing. Inheriting self.font() keeps them on the real UI sans.
        font = QFont(self.font())
        font.setPointSize(point_size)
        return font

    def _draw_size_label(self, painter: QPainter, sel: QRect, source: QRectF) -> None:
        label = f"{int(source.width())} × {int(source.height())}"
        painter.setFont(self._label_font(12))
        text_w = painter.fontMetrics().horizontalAdvance(label) + 12
        box = QRect(sel.x(), max(sel.y() - 24, 2), text_w, 20)
        painter.fillRect(box, QColor(0, 0, 0, 160))
        painter.setPen(Qt.white)
        painter.drawText(box, Qt.AlignCenter, label)

    def _draw_window_label(self, painter: QPainter, sel: QRect, window: WindowInfo) -> None:
        text = window.owner
        if window.title:
            text = f"{window.owner} · {window.title}"
        painter.setFont(self._label_font(12))
        text_w = painter.fontMetrics().horizontalAdvance(text) + 16
        box = QRect(sel.x(), max(sel.y() - 26, 2), min(text_w, sel.width() or text_w), 22)
        painter.fillRect(box, QColor(0, 0, 0, 180))
        painter.setPen(Qt.white)
        painter.drawText(box.adjusted(8, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft, text)

    def _paint_guides(self, painter: QPainter) -> None:
        # Full-span blue guide lines that cross at the pointer, so its position
        # is easy to locate against the dimmed desktop. Clipped to the monitor
        # the pointer is on — on a multi-monitor virtual desktop, striping the
        # lines across every screen would just be noise. Each line is split by a
        # small gap at the centre so the exact target pixel stays uncovered (the
        # loupe and the white crosshair drawn on top read it precisely).
        cx, cy = self._cursor.x(), self._cursor.y()
        bounds = rect_containing(self._monitors, cx, cy)
        if bounds is None:  # pointer outside every reported screen — span overlay
            rect = self.rect()
            left, top, right, bottom = rect.left(), rect.top(), rect.right(), rect.bottom()
        else:
            bx, by, bw, bh = bounds
            left, top, right, bottom = bx, by, bx + bw, by + bh
        gap = 3.0
        painter.save()
        painter.setPen(QPen(_ACCENT, 1))
        painter.drawLine(QPointF(left, cy), QPointF(cx - gap, cy))
        painter.drawLine(QPointF(cx + gap, cy), QPointF(right, cy))
        painter.drawLine(QPointF(cx, top), QPointF(cx, cy - gap))
        painter.drawLine(QPointF(cx, cy + gap), QPointF(cx, bottom))
        painter.restore()

    def _paint_cursor(self, painter: QPainter) -> None:
        # Stand-in for the (hidden) OS cursor, pinned to the overlay's own
        # pointer state so it follows the arrow keys even where QCursor.setPos
        # can't move the real pointer (Wayland; macOS without Accessibility).
        # Drawn as a white crosshair over a darker, thicker underlay so it reads
        # against both the dimmed desktop and a bright spotlit window. A gap at
        # the centre leaves the exact target pixel uncovered.
        cx, cy = self._cursor.x(), self._cursor.y()
        arm, gap = 11.0, 3.0
        segments = (
            (cx - arm, cy, cx - gap, cy),
            (cx + gap, cy, cx + arm, cy),
            (cx, cy - arm, cx, cy - gap),
            (cx, cy + gap, cx, cy + arm),
        )
        painter.save()
        for pen in (QPen(QColor(0, 0, 0, 160), 3), QPen(QColor(255, 255, 255, 235), 1)):
            painter.setPen(pen)
            for x1, y1, x2, y2 in segments:
                painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        painter.restore()

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
        painter.setFont(self._label_font(10))
        painter.setPen(Qt.white)
        painter.drawText(box, Qt.AlignCenter, label)

    def _draw_hint(self, painter: QPainter) -> None:
        hint = t("smart.hint")
        painter.setFont(self._label_font(14))
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
        self._pointer_moved(event.position())

    def _pointer_moved(self, pos: QPointF) -> None:
        # Shared by real mouse moves and keyboard nudges (which apply the move
        # locally as well as warping the OS cursor — see _nudge_cursor).
        self._cursor = pos
        if self._origin is not None:
            self._current = pos
            if not self._dragging:
                dx = pos.x() - self._origin.x()
                dy = pos.y() - self._origin.y()
                if (dx * dx + dy * dy) ** 0.5 > _DRAG_THRESHOLD:
                    self._dragging = True
            self._refresh()
            return
        hover = window_at_point(self._boxes, pos.x(), pos.y())
        previous = self._pending_hover
        self._pending_hover = hover
        if hover == self._hover:
            self._hover_timer.stop()  # wandered back to the current target
        elif self._hover_switch_delay_ms == 0:
            self._commit_hover()
        elif self._hover_switch_delay_ms != HOVER_SWITCH_NEVER and (
            hover != previous or not self._hover_timer.isActive()
        ):
            # The timer is not restarted while the candidate stays the same,
            # so moving around inside one window still commits it.
            self._hover_timer.start()
        # The loupe follows every move, so repaint unconditionally.
        self._refresh()

    def _commit_hover(self) -> None:
        self._hover = self._pending_hover
        self._schedule_preview()
        self._refresh()

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
            self._refresh()
        else:
            # The pointer moved on while this fetch ran; arm a fetch for the
            # now-hovered window (if any) so it isn't starved by the busy gate.
            self._schedule_preview()

    def closeEvent(self, event) -> None:
        # No new preview fetches once the overlay is going away; an in-flight
        # worker may still finish, but its result is dropped above.
        self._closed = True
        self._hover_timer.stop()
        self._preview_timer.stop()
        super().closeEvent(event)

    def leaveEvent(self, event) -> None:
        self._cursor = None
        self._refresh()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        self._press(event.position(), event.button())

    def _press(self, pos: QPointF, button) -> None:
        # ``pos`` is in virtual-desktop coords; a per-screen view translates its
        # local event before calling this so the shared logic stays coordinate-free.
        if button == Qt.RightButton:
            self._cancel()
            return
        if button == Qt.LeftButton:
            # A quick move-and-click means "the thing under the cursor", even
            # when the debounced highlight hasn't caught up yet (and this is
            # the only way the highlight moves under HOVER_SWITCH_NEVER).
            self._commit_pending_hover()
            self._origin = pos
            self._current = pos
            self._dragging = False
            self._press_hover = self._hover
            self._refresh()

    def mouseReleaseEvent(self, event) -> None:
        self._release(event.position(), event.button())

    def _release(self, pos: QPointF, button) -> None:
        if button != Qt.LeftButton or self._origin is None:
            return
        self._current = pos
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
                # Like a click: confirm what's under the pointer, even when the
                # debounced highlight hasn't caught up with it yet.
                self._commit_pending_hover()
                self._accept_target(self._hover)
        elif event.key() in _CURSOR_DELTAS and not event.modifiers() & ~_NUDGE_MODIFIERS:
            self._nudge_cursor(event)

    def _commit_pending_hover(self) -> None:
        if self._pending_hover != self._hover:
            self._hover_timer.stop()
            self._commit_hover()

    def _nudge_cursor(self, event) -> None:
        # Move the real pointer one logical point (Shift: ten) — before a drag
        # to line up its start, or mid-drag to land its edge exactly. The step
        # is based on the overlay's own pointer state, not QCursor.pos(), and
        # the move is applied locally as well as warped: setPos is best-effort —
        # Wayland refuses to warp the pointer at all, and macOS needs the
        # Accessibility permission or it swallows the synthetic event — so the
        # selection, loupe, hover, and the painted crosshair must follow the keys
        # regardless, and repeated presses must accumulate. Where the warp does
        # land its echo arrives on the same coordinates, so applying the move
        # twice is harmless; where it doesn't, the painted crosshair is what the
        # user sees move (the real pointer is hidden).
        dx, dy = _CURSOR_DELTAS[event.key()]
        step = _CURSOR_NUDGE_COARSE if event.modifiers() & Qt.ShiftModifier else 1
        base = self._cursor
        # Pointer state and the warp target are in virtual-desktop coords; map to
        # global via the desktop origin rather than this widget's own position, so
        # a headless multi-screen brain (never shown, so mapToGlobal is unusable)
        # warps the real pointer correctly. On the single window the origin is the
        # window's position, so this matches the old mapTo/FromGlobal behaviour.
        origin = self._geometry.topLeft()
        if base is None:  # no pointer event seen yet (keys-first user)
            global_pos = QCursor.pos()
            base = QPointF(global_pos.x() - origin.x(), global_pos.y() - origin.y())
        target = QPointF(
            min(max(base.x() + dx * step, 0.0), self._geometry.width() - 1.0),
            min(max(base.y() + dy * step, 0.0), self._geometry.height() - 1.0),
        )
        QCursor.setPos(QPoint(int(target.x()) + origin.x(), int(target.y()) + origin.y()))
        self._pointer_moved(target)

    def _accept_region(self) -> None:
        sel = self._selection()
        if sel.width() < _MIN_SIZE or sel.height() < _MIN_SIZE:
            self._cancel()
            return
        # Settle the outcome before emitting: the receiver opens (and
        # activates) the editor synchronously, and the deactivation that
        # follows must not be mistaken for an abandon (see changeEvent).
        self._closed = True
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
        self._closed = True  # one outcome only — see _accept_region
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
        self._closed = True  # one outcome only — see _accept_region
        self.cancelled.emit()
        self.close()


class _ScreenOverlay(QWidget):
    """One per-screen window mirroring a shared :class:`SmartOverlay` brain.

    Holds no capture state: it paints the brain's frame translated into its own
    screen's local space and forwards input (translated to virtual-desktop
    coords) back to the brain, which owns all selection/hover/loupe logic. The
    controller creates one per :class:`QScreen` so every display gets a window:
    macOS, where a single virtual-desktop window only composites on one display
    under per-display Spaces, and Wayland, where a single fullscreen surface only
    covers the output it lands on. Each view renders at its own screen's dpr.

    ``fullscreen`` picks how the window owns its display: a Wayland output is
    claimed with ``showFullScreen`` (the compositor ignores a set position /
    stay-on-top hint), while macOS keeps the stay-on-top window and raises its
    NSWindow above the menu bar.
    """

    def __init__(self, brain: SmartOverlay, screen, vorigin: QPoint, *, fullscreen=False) -> None:
        super().__init__()
        self._brain = brain
        self._screen = screen
        self._fullscreen = fullscreen
        self._on_focus_change = None  # set by the controller
        geometry = screen.geometry()
        # This screen's position within the brain's virtual-desktop coords: add
        # it to a local point to get the brain coord, subtract it when painting.
        self._offset = QPointF(geometry.x() - vorigin.x(), geometry.y() - vorigin.y())
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        # Same hidden-cursor + painted-crosshair scheme as the single window.
        self.setCursor(Qt.BlankCursor)
        self.setMouseTracking(True)
        self.setGeometry(geometry)

    def _to_brain(self, pos: QPointF) -> QPointF:
        return QPointF(pos.x() + self._offset.x(), pos.y() + self._offset.y())

    def present(self) -> None:
        if self._fullscreen:
            # Wayland: the compositor owns geometry and stacking and ignores a
            # set position / stay-on-top hint, so each view goes fullscreen on
            # its own output. Pin the screen before the native surface maps so
            # the fullscreen request targets the right monitor.
            self.createWinId()
            handle = self.windowHandle()
            if handle is not None and self._screen is not None:
                handle.setScreen(self._screen)
            self.showFullScreen()
        else:
            from shotquill.ui import macos_window

            self.show()
            self.raise_()
            # Push the native window above the menu bar and onto every Space so
            # the overlay covers the whole display (menu bar included) and shows
            # on the external monitor too. No-op off macOS / without pyobjc.
            macos_window.raise_above_menubar(self)
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        # Brain paints in virtual-desktop coords; shift so this screen's slice
        # lands at local origin. Everything outside the window is clipped away.
        painter.translate(-self._offset)
        self._brain._paint_all(painter)

    def mouseMoveEvent(self, event) -> None:
        self._brain._pointer_moved(self._to_brain(event.position()))

    def mousePressEvent(self, event) -> None:
        self._brain._press(self._to_brain(event.position()), event.button())

    def mouseReleaseEvent(self, event) -> None:
        self._brain._release(self._to_brain(event.position()), event.button())

    def keyPressEvent(self, event) -> None:
        # Keys carry no position; the brain nudges its own pointer state.
        self._brain.keyPressEvent(event)

    def changeEvent(self, event) -> None:
        # The brain's own focus-steal cancel can't run (it is never shown), so
        # the controller watches activation across all views instead.
        if event.type() == QEvent.ActivationChange and self._on_focus_change is not None:
            self._on_focus_change()
        super().changeEvent(event)


class SmartOverlayController:
    """Drives one :class:`SmartOverlay` brain across one window per screen.

    Used where a single window can't cover every display: macOS with more than
    one display (per-display Spaces) and multi-output Wayland (per-output
    fullscreen). The brain is never shown; it holds all state and emits
    ``changed`` whenever it needs a repaint, which fans out to every per-screen
    view. A selection started on one display therefore continues onto another.
    ``fullscreen`` is forwarded to each view to pick how it owns its display
    (Wayland fullscreen vs. macOS menu-bar-level stay-on-top).

    Kept alive by the brain (``brain._controller``), so it shares the brain's
    lifetime: when the brain closes (accept/cancel) the views close with it.
    """

    def __init__(self, brain: SmartOverlay, *, fullscreen=False) -> None:
        self._brain = brain
        self._finished = False
        vorigin = brain._geometry.topLeft()
        self._views = [
            _ScreenOverlay(brain, s, vorigin, fullscreen=fullscreen)
            for s in QGuiApplication.screens()
        ]
        for view in self._views:
            view._on_focus_change = self._on_focus_change
        brain.changed.connect(self._repaint_views)
        # Mirror every terminal outcome to the views immediately (hide before the
        # editor opens so a screensaver-level overlay can't flash over it), then
        # the brain's destruction tears them down for good.
        for signal in (
            brain.region_selected,
            brain.window_selected,
            brain.fullscreen_selected,
            brain.cancelled,
        ):
            signal.connect(self._finish)
        brain.destroyed.connect(self._close_views)

    def present(self) -> None:
        for view in self._live_views():
            view.present()

    def _live_views(self):
        # Views are WA_DeleteOnClose top-levels; skip any whose C++ object is
        # already gone so a late signal (repaint/close after teardown) can't
        # touch a dangling handle.
        import shiboken6

        return [v for v in self._views if shiboken6.isValid(v)]

    def _repaint_views(self) -> None:
        for view in self._live_views():
            view.update()

    def _finish(self, *args) -> None:
        if self._finished:
            return
        self._finished = True
        for view in self._live_views():
            view.hide()

    def _close_views(self) -> None:
        for view in self._live_views():
            view.close()
        self._views = []

    def _on_focus_change(self) -> None:
        # Cancel if focus left every one of our views (a hot corner, Cmd-Tab, a
        # click in another app) — the single-window overlay does the same in its
        # changeEvent. Deferred a tick so focus moving *between* our own views
        # (clicking from one display to another) doesn't trip it.
        if self._finished:
            return

        def check() -> None:
            # Leans on every accept/cancel path setting ``_closed`` (and _finish
            # setting ``_finished``) *before* the outcome signal is emitted: the
            # editor the app opens on accept steals focus and would otherwise
            # read as an abandon here. Both guards run before this deferred tick.
            if self._finished or self._brain._closed:
                return
            if not any(v.isActiveWindow() for v in self._live_views()):
                self._brain._cancel()

        QTimer.singleShot(0, check)


def present_overlay(overlay: SmartOverlay, app) -> None:
    """Show the smart-capture overlay so it covers every display in full.

    On macOS a plain stay-on-top window sits *under* the menu bar (so its top
    strip can't be captured) and, with more than one display, only composites on
    one of them (per-display Spaces). Both are fixed by driving the overlay
    through one :class:`SmartOverlayController`-managed window per screen, each
    raised above the menu bar — so the per-screen path is taken for *all* macOS,
    single display included.

    On Wayland a single fullscreen surface only covers the output it lands on, so
    with more than one display the others get no overlay; there the controller
    gives each output its own fullscreen view. A single-output Wayland session,
    and X11 (one window already spans the whole virtual desktop), present the
    single window directly.
    """
    if sys.platform == "darwin":
        controller = SmartOverlayController(overlay)
        overlay._controller = controller  # share the brain's lifetime
        controller.present()
    elif _compositor_prefers_fullscreen() and len(app.screens()) > 1:
        controller = SmartOverlayController(overlay, fullscreen=True)
        overlay._controller = controller  # share the brain's lifetime
        controller.present()
    else:
        overlay.present()
