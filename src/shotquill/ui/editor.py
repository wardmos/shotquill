# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""The annotation editor window: screenshot + toolbar + copy/save.

By default it opens in *spotlight* mode: frameless (no macOS title bar or
traffic-light buttons) over a translucent dim layer covering the rest of the
desktop, so the shot stays lit in place exactly like the capture overlay
highlighted it. A Settings toggle restores the regular titled window.

A region capture hands over a :class:`RegionContext` (the full-desktop
screenshot it was cropped from) and stays *adjustable* here: until the first
annotation lands, the arrow keys nudge the crop by one screenshot pixel per
press (Shift steps by 10, Option moves the right/bottom edge to resize), and a
mouse press on a crop edge opens a full-screen adjust surface — re-cropping from
the full screenshot. The first annotation freezes the crop.

The edit core (canvas, toolbar, OCR, finish keys, crop re-crop logic) lives in
:class:`~shotquill.ui.editor_core.EditorCoreMixin`, shared with the unified
full-screen :class:`~shotquill.ui.spotlight.SpotlightSurface`. This window is the
*framed* shell: a titled (or frameless-over-backdrop) top-level that re-places
itself over the shot on every crop change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QGuiApplication,
    QImage,
    QKeySequence,
    QPainter,
    QShortcut,
)
from PySide6.QtWidgets import QLabel, QMainWindow, QSizePolicy, QWidget

from shotquill.i18n import t
from shotquill.ocr import get_recognizer
from shotquill.ui import macos_window
from shotquill.ui._debug import crop_log
from shotquill.ui.editor_core import (
    EditorCoreMixin,
    RegionContext,
    _finish_tip,  # noqa: F401 — re-exported for tests
    _toolbar_placement,
)

if TYPE_CHECKING:
    from shotquill.config import Config

__all__ = ["CropHost", "EditorWindow", "RegionContext"]

_MAX_INITIAL_WIDTH = 1400
_MAX_INITIAL_HEIGHT = 900

# Same dim the capture overlay paints over the desktop, so the editor's backdrop
# reads as a continuation of the capture rather than a new layer.
_BACKDROP_DIM = QColor(0, 0, 0, 120)

# OCR status badge shown over the canvas in frameless mode (no title bar to carry
# the text); styled like the overlay's labels.
_BADGE_STYLE = (
    "background-color: rgba(0, 0, 0, 180); color: white;padding: 3px 8px; border-radius: 4px;"
)


class CropHost(Protocol):
    """What :class:`AnnotationCanvas` calls back to enter mouse crop-adjustment.

    The framed editor implements this; the canvas detects a press on a crop edge
    (while the crop is still adjustable) and hands off to ``enter_crop_adjust``,
    which opens the full-screen adjust surface. (The unified SpotlightSurface
    owns its own handles and never uses this path.)
    """

    def crop_adjustable(self) -> bool: ...
    def enter_crop_adjust(self, edges: tuple[bool, bool, bool, bool]) -> None: ...


class _EditorBackdrop(QWidget):
    """A translucent dim layer behind a frameless editor.

    Covers the whole virtual desktop with the same dim the capture overlay used,
    so editing feels like the capture's spotlight never went away: the shot stays
    lit while everything around it stays dark. Deliberately inert — it never
    takes focus (the editor keeps keyboard input) and clicks on it do nothing, so
    a stray click can't discard annotations. The editor shows and hides it with
    its own activation and closes it from ``closeEvent``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.Window
            | Qt.FramelessWindowHint
            | Qt.WindowDoesNotAcceptFocus
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setGeometry(QGuiApplication.primaryScreen().virtualGeometry())

    def paintEvent(self, event) -> None:
        QPainter(self).fillRect(self.rect(), _BACKDROP_DIM)


class EditorWindow(EditorCoreMixin, QMainWindow):
    #: Emitted when the user pins the shot to the desktop: the annotated image
    #: plus the capture's on-screen rect (or None) so the pin can size itself
    #: for the screen the shot came from.
    pin_requested = Signal(QImage, object)
    #: Internal: OCR finished on its worker thread — (lines, error). The queued
    #: delivery hops back to the GUI thread before touching clipboard/title.
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
        self.setWindowTitle(t("title.annotate"))
        self._placed = False
        # The full-screen crop-adjust surface, alive only while the user is
        # dragging the crop's edges (see enter_crop_adjust).
        self._crop_overlay = None

        # Spotlight mode (default, toggleable in Settings): the editor opens
        # frameless — no macOS title bar or traffic lights — over a dim backdrop,
        # so the shot stays lit in place against the darkened desktop exactly
        # like the capture overlay showed it.
        self._backdrop: _EditorBackdrop | None = None
        self._status_badge: QLabel | None = None
        if config.editor_backdrop():
            self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
            self._backdrop = _EditorBackdrop()

        # get_recognizer is resolved here (a module the tests can patch) and
        # handed to the core, which omits the OCR button when it is None.
        toolbar = self._init_editor_core(
            image, config, origin, region, get_recognizer(), split_outputs=True
        )
        self.setCentralWidget(self._canvas)
        # Region captures stay adjustable: let the canvas hand off an edge press
        # to this window, which opens the full-screen adjust surface.
        if self._region is not None:
            self._canvas.set_crop_host(self)
        if self._backdrop is not None:
            # Frameless means no title bar to carry the OCR status text; a small
            # badge over the canvas shows it instead (see _set_status).
            self._status_badge = QLabel(self._canvas.viewport())
            self._status_badge.setStyleSheet(_BADGE_STYLE)
            self._status_badge.hide()

        # The tool row lands in the corner nearest the pointer (e.g. the
        # bottom-right after a region drag towards the bottom of the screen), so
        # finishing a shot never means crossing the whole capture.
        area, align_right = _toolbar_placement(QCursor.pos(), origin)
        self.addToolBar(area, toolbar)
        # The copy/save finish buttons never fold, so they go on a sibling bar in
        # the opposite edge's area rather than sharing the tool row's. Sharing a
        # row would peg the window's minimum width to the sum of both bars — wider
        # than a narrow shot, which then stretches the canvas off the capture; in
        # separate areas the minimum is just the wider single bar. It also keeps
        # each edge a single row, so the shot still lands exactly on its capture.
        outputs = toolbar.outputs_toolbar
        if align_right:
            # Hug copy/save to the pointer's horizontal side: a leading expanding
            # spacer pushes them to the trailing edge of their (slack-absorbing,
            # since it's the only bar in its area) row.
            spacer = QWidget()
            spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            outputs.insertWidget(outputs.actions()[0], spacer)
        opposite = Qt.BottomToolBarArea if area == Qt.TopToolBarArea else Qt.TopToolBarArea
        self.addToolBar(opposite, outputs)
        # Resolves the (configurable, possibly disabled) finish keys and sets the
        # matching tooltips; re-run by the app whenever Settings changes.
        self.reload_finish_keys()

        # Size from the shot's on-screen (logical) rect when known — the image is
        # in native pixels, which is 2x too large on Retina displays.
        initial = origin.size() if origin is not None else image.size()
        self.resize(
            min(initial.width(), _MAX_INITIAL_WIDTH) + 40,
            min(initial.height(), _MAX_INITIAL_HEIGHT) + 120,
        )

        close_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        close_shortcut.activated.connect(self.close)

        self._wire_adjust_hint()

    def showEvent(self, event) -> None:
        self._escape_guard.enable()
        super().showEvent(event)
        if self._backdrop is not None:
            # Shown without activating, so the editor keeps keyboard focus;
            # raise_ keeps the editor above the dim layer.
            self._backdrop.show()
            self.raise_()
        if not self._placed:
            self._placed = True
            if self._origin is not None:
                self._place_over_origin()
            else:
                self._fit_unplaced_to_screen()
        self._canvas.fitInView(self._canvas.sceneRect(), Qt.KeepAspectRatio)

    def changeEvent(self, event) -> None:
        # The dim backdrop tracks the editor's activation: it hides while the
        # user is in another app (it must not darken whatever they switched to)
        # and comes back when the editor regains activation. Visibility is
        # guarded so the deactivation that accompanies closing can't resurrect a
        # backdrop that closeEvent already took down.
        if (
            event.type() == QEvent.ActivationChange
            and self._backdrop is not None
            and self.isVisible()
        ):
            if self.isActiveWindow():
                self._backdrop.show()
                self.raise_()
            else:
                self._backdrop.hide()
        if event.type() == QEvent.ActivationChange and self.isActiveWindow():
            self._canvas.restore_text_focus()
        super().changeEvent(event)

    def closeEvent(self, event) -> None:
        self._escape_guard.disable()
        # Before teardown fires a focus-out on any active text item, tell the
        # canvas to stop committing it onto the dying undo stack.
        self._canvas.begin_teardown()
        # The crop-adjust surface is a separate top-level window only this editor
        # holds; close it with the editor (e.g. on Cmd-Q while it is up) so it
        # can't outlive its host. Disconnect first so its teardown can't call
        # back into this (WA_DeleteOnClose) editor as it is destroyed.
        if self._crop_overlay is not None:
            overlay, self._crop_overlay = self._crop_overlay, None
            overlay.region_selected.disconnect(self._crop_adjusted)
            overlay.destroyed.disconnect(self._crop_adjust_finished)
            overlay.close()
        if self._backdrop is not None:
            self._backdrop.close()
            self._backdrop.deleteLater()
            self._backdrop = None
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:
        # Keep the whole image fitted as the window is (re-)sized — with
        # scrollbars off there is no other way to reach content outside the
        # viewport.
        super().resizeEvent(event)
        crop_log(f"resizeEvent size={event.size()} crop_overlay={self._crop_overlay is not None}")
        # While the full-screen adjust surface is up, never re-fit: a stray
        # resize must not scale the frozen shot under the user (the surface owns
        # the live preview, and the editor re-crops once on apply).
        if self._crop_overlay is not None:
            return
        self._canvas.fitInView(self._canvas.sceneRect(), Qt.KeepAspectRatio)

    def keyPressEvent(self, event) -> None:
        # Crop nudge (arrows) + finish keys live in the shared core; anything it
        # declines falls through to the default handling.
        if self.handle_key(event):
            return
        super().keyPressEvent(event)

    def place_for_selection(self, origin: QRect) -> None:
        # The framed shell re-places its top-level window over the new crop.
        self._place_over_origin()
        self._canvas.fitInView(self._canvas.sceneRect(), Qt.KeepAspectRatio)

    def _fit_unplaced_to_screen(self) -> None:
        """Keep an origin-less editor, such as a long screenshot, fully reachable."""
        self.layout().activate()
        viewport = self._canvas.viewport()
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()

        # The requested viewport may be capped at 900 px, yet its two toolbar
        # rows and window frame can still make the whole editor taller than the
        # desktop. Shrink only the viewport; fitInView scales the image afterward.
        chrome = self.frameGeometry().size() - viewport.size()
        target = QSize(
            min(viewport.width(), max(1, available.width() - chrome.width())),
            min(viewport.height(), max(1, available.height() - chrome.height())),
        )
        self.resize(self.size() + (target - viewport.size()))

        # No capture origin exists to position a long image, so retain the window
        # manager placement where possible and clamp only the off-screen portion.
        frame = self.frameGeometry()
        max_left = max(available.left(), available.right() - frame.width() + 1)
        max_top = max(available.top(), available.bottom() - frame.height() + 1)
        clamped = QPoint(
            min(max(frame.left(), available.left()), max_left),
            min(max(frame.top(), available.top()), max_top),
        )
        self.move(self.pos() + (clamped - frame.topLeft()))

    def _place_over_origin(self) -> None:
        """Open the editor so the screenshot appears to stay where it was shot.

        Sizes the window so the canvas viewport matches the capture's on-screen
        size (within the initial-size caps), shifts the frame so the canvas lands
        exactly on the capture rect, then clamps the frame to the screen so the
        toolbar and window edges stay reachable. Runs on first show — only then
        are the toolbar and frame dimensions known.
        """
        if self._backdrop is not None:
            # Frameless spotlight window: stop the user from drag-resizing it.
            # Resizing only scales the shot via fitInView (never useful) and, on
            # macOS, the OS treats the frameless edges as resize handles — so an
            # edge drag meant to adjust the crop is hijacked as a window resize
            # and never reaches the canvas. Clearing the resizable style mask
            # removes that zone (the program still re-places the window itself).
            # Done before the move below so any AppKit frame nudge is corrected.
            macos_window.set_resizable(self, False)
        self.layout().activate()  # settle toolbar/central layout before measuring
        viewport = self._canvas.viewport()
        screen = QGuiApplication.screenAt(self._origin.center())
        available = (screen or self.screen()).availableGeometry()
        # The frame is the viewport plus chrome (toolbar, window borders). A
        # near-screen-sized capture plus that chrome would not fit, leaving the
        # toolbar off-screen — often exactly where the pointer placed it (a region
        # drag ends bottom-right, so the toolbar lands at the clipped bottom
        # edge). Cap the viewport so the whole frame fits; fitInView scales the
        # shot down to match.
        chrome = self.frameGeometry().size() - viewport.size()
        target = QSize(
            min(self._origin.width(), _MAX_INITIAL_WIDTH, available.width() - chrome.width()),
            min(self._origin.height(), _MAX_INITIAL_HEIGHT, available.height() - chrome.height()),
        )
        self.resize(self.size() + (target - viewport.size()))

        delta = self._origin.topLeft() - viewport.mapToGlobal(QPoint(0, 0))
        frame = self.frameGeometry().translated(delta)
        max_left = max(available.left(), available.right() - frame.width() + 1)
        max_top = max(available.top(), available.bottom() - frame.height() + 1)
        frame.moveLeft(min(max(frame.left(), available.left()), max_left))
        frame.moveTop(min(max(frame.top(), available.top()), max_top))
        self.move(self.pos() + (frame.topLeft() - self.frameGeometry().topLeft()))

    # --- mouse crop-adjustment (CropHost; the canvas hands off an edge press) --

    def enter_crop_adjust(self, edges: tuple[bool, bool, bool, bool]) -> None:
        """Open the full-screen adjust surface seeded with the current crop.

        The canvas calls this when the user presses a crop edge. The surface
        re-crops from the frozen full-desktop screenshot and, on apply, hands the
        new selection back to :meth:`_crop_adjusted`; cancelling leaves the crop
        unchanged. Doing the drag on one fixed full-screen window — rather than
        resizing this small window live — keeps the surrounding desktop visible
        and avoids any window-geometry feedback under the cursor.
        """
        from PySide6.QtWidgets import QApplication

        from shotquill.ui.smart_overlay import CropAdjustOverlay, present_overlay

        crop_log(f"enter_crop_adjust edges={edges} origin={self._origin}")
        if self._crop_overlay is not None:  # a session is already up
            return
        overlay = CropAdjustOverlay(self._region.screenshot, self._region.geometry, self._origin)
        overlay.region_selected.connect(self._crop_adjusted)
        overlay.destroyed.connect(self._crop_adjust_finished)
        self._crop_overlay = overlay
        present_overlay(overlay, QApplication.instance())
        # Continue the still-held press as a resize of the grabbed edge, so the
        # gesture feels unbroken — but only on the single-window path, where a
        # mouse grab on the shown overlay actually delivers the rest of the drag.
        # The multi-screen controller (always used on macOS, and multi-output
        # Wayland) shows per-screen views, not this brain, so a grab here would
        # never see the release and would leave a stale drag that then fires on
        # hover; there the user just grabs a drawn handle. ``_controller`` is set
        # by present_overlay only on those multi-screen paths.
        if getattr(overlay, "_controller", None) is None:
            overlay.begin_resize(edges)

    def _crop_adjusted(self, image: QImage, rect: QRect) -> None:
        # Re-crop from the original full-desktop screenshot via the same path the
        # arrow keys use, so a single source of truth places the window once.
        crop_log(f"_crop_adjusted rect={rect}")
        self._selection = QRectF(rect)
        self._apply_selection()

    def _crop_adjust_finished(self) -> None:
        # The surface is gone (applied or cancelled); reclaim activation so the
        # editor returns to front and its spotlight backdrop, which tracks
        # activation, comes back (see changeEvent).
        self._crop_overlay = None
        if self.isVisible():
            self.raise_()
            self.activateWindow()
