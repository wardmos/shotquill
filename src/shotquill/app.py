# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Application entry point: a menu-bar-resident Qt app.

Two capture hotkeys: full-screen (``⌥S``) grabs everything immediately, while
smart capture (``⌥A``) opens an overlay that picks its mode from the pointer —
hover a window to shoot it, hover empty space to shoot full screen, or drag to
shoot a region. Both feed the annotation editor. Hotkeys, save directory, image
format, and UI language are editable in Settings.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QProcess, QRect, Qt, QTimer, Signal, Slot
from PySide6.QtGui import (
    QAction,
    QColor,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QProgressDialog, QSystemTrayIcon

from shotquill import __version__, debug_log, headless, permissions, redact, uninstall
from shotquill import allowlist as al
from shotquill import blocklist as bl
from shotquill.autostart import get_manager as get_autostart_manager
from shotquill.capture.base import Rect
from shotquill.config import Config, human_readable_hotkey
from shotquill.desktop_id import LINUX_GUI_DESKTOP_FILE_NAME
from shotquill.headless import CapabilityUnsupported, get_capturer
from shotquill.hotkeys import get_manager as get_hotkey_manager
from shotquill.hotkeys.base import HotkeyUnavailable
from shotquill.i18n import set_language, t
from shotquill.imaging import result_to_qimage
from shotquill.scroll import get_scroller
from shotquill.stitch import NoScrollingDetected, ScrollAccumulator, StitchError
from shotquill.ui.editor import EditorWindow, RegionContext
from shotquill.ui.editor_core import EditorCoreMixin
from shotquill.ui.feedback import CaptureFeedback
from shotquill.ui.pinned import PinnedWindow
from shotquill.ui.scrolling_status import ScrollingStatus
from shotquill.ui.settings import SettingsDialog
from shotquill.ui.smart_overlay import SmartOverlay, present_overlay

_LOG = debug_log.get_logger(__name__)
_UNINSTALL_LAUNCH_TIMEOUT_MS = 45_000
_UNINSTALL_TERM_GRACE_MS = 2_000
_UNINSTALL_TIMEOUT_DETAIL = "the protected uninstall launcher timed out"


def _build_icon() -> QIcon:
    """Build the menu-bar / tray mark.

    On macOS we render a *template* image: monochrome, only the alpha channel
    matters, and macOS tints the opaque pixels to match the menu bar (white on
    dark, dark on light) like its own status items — so only the capture/pen mark
    is opaque and the surrounding tile is transparent. Other desktops (Linux/X11
    tray) don't tint a mask, so there we draw a self-contained icon: a blue tile
    with the mark painted in white. The colored Launchpad icon is a separate
    ``.icns`` and is unaffected.

    For non-macOS tray icons we attach multiple pixel sizes (16/22/24/32/48/64)
    so Qt can pick the right one for a HiDPI panel; a single 64px pixmap would
    otherwise be downscaled per-frame and read as a soft blob on standard-DPI
    panels and a blurry one on HiDPI.
    """
    is_mac = sys.platform == "darwin"
    if is_mac:
        # macOS reads a single template pixmap; AppKit composites it per-DPI.
        pixmap = _render_tray_pixmap(64, is_mac=True)
        icon = QIcon(pixmap)
        icon.setIsMask(True)  # tell macOS to render it as a template image
        return icon
    icon = QIcon()
    for size in (16, 22, 24, 32, 48, 64):
        icon.addPixmap(_render_tray_pixmap(size, is_mac=False))
    return icon


def _render_tray_pixmap(size: int, *, is_mac: bool) -> QPixmap:
    """Render the rounded-square capture/pen tray glyph at ``size``×``size`` pixels.

    The proportions (corner radius, tile padding, glyph height) are all derived
    from ``size`` so smaller pixmaps stay legible. macOS gets an inverted
    template mask: the mark is opaque and the tile is transparent. Everywhere
    else the mark is painted white over a blue tile so it reads on dark panels
    without relying on the desktop to tint it.
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    tile_color = QColor("black") if is_mac else QColor("#087cf3")
    mark_color = QColor("black") if is_mac else QColor("white")
    if not is_mac:
        painter.setBrush(tile_color)
        padding = max(1, round(size * 6 / 64))
        radius = max(2, round(size * 14 / 64))
        tile = size - 2 * padding
        painter.drawRoundedRect(padding, padding, tile, tile, radius, radius)

    painter.scale(size / 64, size / 64)
    if is_mac:
        painter.translate(32, 32)
        painter.scale(1.60, 1.60)
        painter.translate(-32, -32)
    pen = QPen(mark_color, 4.0)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    _draw_tray_corners(painter)
    painter.setPen(Qt.NoPen)
    painter.setBrush(mark_color)
    _draw_tray_nib(painter)
    if is_mac:
        painter.setCompositionMode(QPainter.CompositionMode_DestinationOut)
        painter.setBrush(QColor("black"))
    else:
        painter.setBrush(tile_color)
    painter.drawEllipse(31, 35, 3, 3)
    painter.drawRect(32, 38, 1, 6)
    painter.end()
    return pixmap


def _draw_tray_corners(painter: QPainter) -> None:
    """Draw the four viewfinder corners on a 64×64 design grid."""
    painter.drawLine(18, 19, 26, 19)
    painter.drawLine(18, 19, 18, 27)
    painter.drawLine(46, 19, 38, 19)
    painter.drawLine(46, 19, 46, 27)
    painter.drawLine(18, 45, 26, 45)
    painter.drawLine(18, 45, 18, 37)
    painter.drawLine(46, 45, 38, 45)
    painter.drawLine(46, 45, 46, 37)


def _draw_tray_nib(painter: QPainter) -> None:
    """Draw the central pen nib on a 64×64 design grid."""
    nib = QPainterPath()
    nib.moveTo(29.3, 44.8)
    nib.cubicTo(28.5, 40.0, 27.1, 37.2, 25.1, 34.8)
    nib.cubicTo(27.1, 28.4, 31.7, 24.8, 39.5, 23.0)
    nib.cubicTo(38.5, 28.4, 35.1, 31.6, 30.1, 33.6)
    nib.cubicTo(32.9, 33.6, 35.9, 33.0, 38.1, 32.0)
    nib.cubicTo(36.3, 35.4, 36.1, 38.6, 37.7, 41.6)
    nib.cubicTo(35.3, 43.6, 34.1, 44.8, 33.3, 44.8)
    nib.closeSubpath()
    painter.drawPath(nib)
    painter.drawRoundedRect(30, 44, 6, 2, 1, 1)


class _HotkeyBridge(QObject):
    """Marshals hotkey events from the pynput listener thread onto the Qt main thread."""

    smart_requested = Signal()
    fullscreen_requested = Signal()


@dataclass
class _ScrollSession:
    """One in-flight long-screenshot capture: its timer, growing stitch, scroller,
    framed region, and the blocklist snapshot used to redact each sampled frame.

    Bundled so the lifecycle is atomic — set as one ``self._scroll`` and torn down
    in one place — rather than four parallel attributes that must move in lockstep.
    """

    timer: QTimer
    accumulator: ScrollAccumulator
    scroller: object
    status: ScrollingStatus
    rect: Rect
    blocklist: object
    operation_id: str


class _UninstallProbeBridge(QObject):
    """Returns slow installer inspection results to the Qt thread."""

    finished = Signal(int, object, object)


class ShotquillApp(QObject):
    """Owns the tray icon, hotkeys, capturer, overlays, and editor windows.

    Subclasses ``QObject`` so it has main-thread affinity: the hotkey bridge's
    signals are emitted from pynput's listener thread, and an ``AutoConnection``
    to a QObject slot on the main thread is delivered queued (i.e. capture runs
    on the GUI thread). A plain-object receiver would instead be called directly
    on the listener thread, where creating Qt windows crashes on macOS.
    """

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        self._config = Config()
        debug_log.configure(self._config)
        _LOG.debug("gui init platform=%s version=%s", sys.platform, __version__)
        set_language(self._config.language())

        # Platform backends behind the factory seams: macOS gets ScreenCaptureKit
        # + key-code hotkeys + a LaunchAgent; Linux/X11 gets a QScreen grab +
        # pynput hotkeys + an XDG autostart entry.
        self._capturer = get_capturer(include_cursor=self._config.include_cursor())
        self._hotkeys = get_hotkey_manager()
        self._feedback = CaptureFeedback()
        self._autostart = get_autostart_manager()
        self._sync_autostart()
        self._windows: list[object] = []  # keep overlays/editors alive
        self._settings_dialog: SettingsDialog | None = None
        self._settings_shelved = False  # Settings hidden while a capture runs
        self._uninstall_probe_running = False
        self._uninstall_probe_generation = 0
        self._uninstall_probe_thread: threading.Thread | None = None
        self._uninstall_process: QProcess | None = None
        self._uninstall_launch_timer: QTimer | None = None
        self._uninstall_kill_timer: QTimer | None = None
        self._uninstall_launcher_timed_out = False
        self._uninstall_progress: QProgressDialog | None = None
        self._uninstall_interaction_suspended = False
        self._capture_actions: tuple[QAction, ...] = ()
        self._quit_action: QAction | None = None
        self._scrolling_action: QAction | None = None
        self._pending_permission_prompt: str | None = None
        # Blocklisted windows on screen for the current smart-capture session
        # (id → window); refused on click, skipped in the hover preview.
        self._blocked_windows: dict[int, object] = {}
        # When the allowlist is enabled, the on-screen windows *not* on it for the
        # current smart-capture session (id → window); refused on click, skipped
        # in the hover preview — the same treatment blocklisted windows get, since
        # an allowlist means "only the listed apps may be captured".
        self._not_allowed_windows: dict[int, object] = {}
        # The allowlist in force for the current smart-capture session, so the
        # region / full-screen selection handlers (which fire later, from overlay
        # signals) can refuse whole-screen grabs while it is enabled.
        self._allowlist = al.Allowlist()
        self._smart_screenshot = None
        self._smart_geometry = None
        # The live long-screenshot session, set up by _scrolling_region_selected and
        # driven by a QTimer tick; ``None`` when no scroll capture is in flight.
        self._scroll: _ScrollSession | None = None
        self._current_debug_id: str | None = None
        self._smart_debug_id: str | None = None

        self._bridge = _HotkeyBridge()
        # Hotkey backends may emit from a platform callback thread. Force queued
        # delivery so capture code always runs on Qt's GUI thread.
        self._bridge.smart_requested.connect(
            self._capture_smart, Qt.ConnectionType.QueuedConnection
        )
        self._bridge.fullscreen_requested.connect(
            self._capture_fullscreen, Qt.ConnectionType.QueuedConnection
        )
        self._uninstall_probe_bridge = _UninstallProbeBridge(self)
        self._uninstall_probe_bridge.finished.connect(self._uninstall_probe_finished)

        self._tray = QSystemTrayIcon(_build_icon(), self._app)
        self._tray.setToolTip("ShotQuill")
        self._rebuild_menu()
        self._tray.show()
        self._app.applicationStateChanged.connect(self._retry_pending_permissions)
        self._apply_permission_gated_hotkeys()
        _LOG.debug("gui ready")

    def _hotkey_label(self, action: str) -> str:
        """Display string for a hotkey, or empty if the user disabled it."""
        if not self._config.hotkey_enabled(action):
            return ""
        return human_readable_hotkey(self._config.hotkey(action))

    def _rebuild_menu(self) -> None:
        """(Re)build the tray menu — used at startup and after a language change."""
        menu = QMenu()
        smart_key = self._hotkey_label("smart_capture")
        fullscreen_key = self._hotkey_label("fullscreen_capture")

        smart = QAction(f"{t('menu.smart')}\t{smart_key}", menu)
        smart.triggered.connect(self._capture_smart)
        fullscreen = QAction(f"{t('menu.fullscreen')}\t{fullscreen_key}", menu)
        fullscreen.triggered.connect(self._capture_fullscreen)
        scrolling = QAction(t("menu.scrolling"), menu)
        scrolling.triggered.connect(self._capture_scrolling)
        captures_enabled = not self._uninstall_interaction_suspended
        smart.setEnabled(captures_enabled)
        fullscreen.setEnabled(captures_enabled)
        scrolling.setEnabled(captures_enabled)
        open_folder = QAction(t("menu.open_folder"), menu)
        open_folder.triggered.connect(self._open_save_folder)
        settings = QAction(t("menu.settings"), menu)
        settings.triggered.connect(self._open_settings)
        about = QAction(t("menu.about"), menu)
        about.triggered.connect(self._show_about)
        quit_action = QAction(t("menu.quit"), menu)
        quit_action.setEnabled(self._uninstall_process is None)
        quit_action.triggered.connect(self._request_quit)

        menu.addAction(smart)
        menu.addAction(fullscreen)
        menu.addAction(scrolling)
        menu.addSeparator()
        menu.addAction(open_folder)
        menu.addAction(settings)
        menu.addAction(about)
        menu.addSeparator()
        menu.addAction(quit_action)

        self._tray.setContextMenu(menu)
        self._menu = menu  # keep a reference
        self._capture_actions = (smart, fullscreen, scrolling)
        self._scrolling_action = scrolling
        self._set_scrolling_action(self._scroll is not None)
        self._quit_action = quit_action

    def _set_scrolling_action(self, active: bool) -> None:
        """Make the tray action double as a visible stop/progress affordance."""
        action = self._scrolling_action
        if action is None:
            return
        if active:
            frames = self._scroll.accumulator.frame_count if self._scroll is not None else 0
            action.setText(f"{t('menu.scrolling_stop')} · {frames}")
            self._tray.setToolTip(t("tray.scrolling_progress").format(frames=frames))
        else:
            action.setText(t("menu.scrolling"))
            self._tray.setToolTip("ShotQuill")

    def _request_quit(self) -> None:
        """Keep the app alive until the uninstall coordinator is ready."""
        if self._uninstall_process is not None:
            return
        self._app.quit()

    def _apply_permission_gated_hotkeys(self) -> None:
        if self._needs_screen_recording_permission():
            self._hotkeys.clear()
            self._show_permission_prompt("screen_recording")
            return
        if self._apply_hotkeys():
            self._pending_permission_prompt = None

    def _needs_screen_recording_permission(self) -> bool:
        if sys.platform != "darwin":
            return False
        return permissions.screen_capture_status() is permissions.PermissionStatus.DENIED

    def _show_permission_prompt(self, permission: str) -> None:
        if self._pending_permission_prompt == permission:
            return
        self._pending_permission_prompt = permission
        if permission == "screen_recording":
            self._notify(t("notify.capture_need_screen_recording"))
            permissions.open_screen_capture_pane()
        elif permission == "input_monitoring":
            self._notify(t("notify.hotkeys_need_input_monitoring"))
            permissions.open_input_monitoring_pane()

    def _retry_pending_permissions(self, state: Qt.ApplicationState) -> None:
        if state == Qt.ApplicationState.ApplicationActive and self._pending_permission_prompt:
            self._apply_permission_gated_hotkeys()

    def _apply_hotkeys(self) -> bool:
        # Note: no stop() here. Backends handle re-applying settings internally:
        # Carbon re-registers shortcuts, Wayland refreshes portal bindings, and
        # pynput-backed platforms avoid unsafe listener restarts.
        self._hotkeys.clear()
        # The description is shown in the compositor's own shortcuts settings on
        # the Wayland (GlobalShortcuts portal) backend; the pynput backends ignore
        # it. Use the same labels the tray menu shows so the action reads the same
        # in both places.
        actions = (
            ("smart_capture", self._bridge.smart_requested.emit, t("menu.smart")),
            ("fullscreen_capture", self._bridge.fullscreen_requested.emit, t("menu.fullscreen")),
        )
        for action, emit, label in actions:
            if self._config.hotkey_enabled(action):
                _LOG.debug(
                    "register hotkey action=%s combo=%s", action, self._config.hotkey(action)
                )
                self._hotkeys.register(self._config.hotkey(action), emit, description=label)
        try:
            self._hotkeys.start()
        except HotkeyUnavailable as exc:
            # The session refuses global grabs (e.g. Wayland). Nothing to grant
            # — surface the reason once and keep the tray menu working.
            _LOG.exception("hotkeys unavailable reason=%s", exc.reason)
            self._notify(t("notify.hotkeys_unavailable").format(reason=exc.reason))
        except PermissionError:
            # The deep-link is macOS-specific (an x-apple-systempreferences URL
            # opened via `open`); skip it elsewhere where it would be a no-op.
            if sys.platform == "darwin":
                self._show_permission_prompt("input_monitoring")
            else:
                self._notify(t("notify.hotkeys_need_input_monitoring"))
            _LOG.exception("hotkey permission error")
            return False
        _LOG.debug("hotkeys active")
        return True

    def _shelve_settings_dialog(self) -> None:
        """Hide an open (modeless) Settings window while a capture runs.

        Two reasons. The capture overlay cancels itself when it loses
        activation (hot corners, Cmd-Tab — see SmartOverlay.changeEvent), and
        a modeless Settings window contends for activation the moment the
        overlay appears, wedging the capture flow. And ShotQuill's own UI
        doesn't belong in the shot. Hidden rather than closed, so in-progress
        edits survive; ``_unshelve_settings_dialog`` brings it back.
        """
        dialog = self._settings_dialog
        if dialog is None or not dialog.isVisible():
            return
        self._settings_shelved = True
        dialog.hide()
        # Give the hide a chance to reach the window server before the grab.
        self._app.processEvents()

    def _unshelve_settings_dialog(self) -> None:
        if not self._settings_shelved:
            return
        if self._scroll is not None:
            return  # long capture is still sampling; keep ShotQuill out of its frames
        if any(isinstance(window, SmartOverlay) for window in self._windows):
            return  # another capture overlay is still up; its close retries
        self._settings_shelved = False
        if self._settings_dialog is not None:
            self._settings_dialog.show()

    @Slot()
    def _capture_fullscreen(self) -> None:
        if self._uninstall_interaction_suspended:
            return
        if self._scroll is not None:
            self._cancel_scrolling(notify=False)
        operation_id = debug_log.new_operation_id("capture")
        previous = self._current_debug_id
        self._current_debug_id = operation_id
        try:
            _LOG.debug("op=%s capture_fullscreen start", operation_id)
            self._shelve_settings_dialog()
            blocklist = self._load_blocklist_or_abort()
            if blocklist is None:
                self._unshelve_settings_dialog()
                return
            allowlist = self._load_allowlist_or_abort()
            if allowlist is None:
                self._unshelve_settings_dialog()
                return
            if allowlist:
                # An allowlist restricts capture to specific apps, so a whole-screen
                # grab cannot be honoured — refuse it (matching the CLI/MCP contract).
                self._notify(t("notify.allowlist_whole_screen"))
                self._unshelve_settings_dialog()
                _LOG.debug("op=%s capture_fullscreen refused allowlist_enabled=true", operation_id)
                return
            try:
                screenshot = self._grab(blocklist)
            finally:
                self._unshelve_settings_dialog()
            if screenshot is not None:
                _LOG.debug(
                    "op=%s capture_fullscreen deliver size=%sx%s",
                    operation_id,
                    screenshot.width(),
                    screenshot.height(),
                )
                self._deliver_capture(screenshot, self._app.primaryScreen().virtualGeometry())
        finally:
            self._current_debug_id = previous

    @Slot()
    def _capture_smart(self) -> None:
        if self._scroll is not None:
            self._cancel_scrolling(notify=False)
        self._open_smart_overlay(scrolling=False)

    @Slot()
    def _capture_scrolling(self) -> None:
        """Long screenshot: frame a region, then auto-scroll and stitch it."""
        if self._scroll is not None:
            self._cancel_scrolling()
            return
        if self._uninstall_interaction_suspended:
            return
        if self._is_wayland_platform() or not getattr(
            self._capturer, "supports_repeated_region_capture", True
        ):
            reason = (
                "the Wayland Screenshot portal provides only one still; continuous "
                "capture needs ScreenCast/PipeWire support"
            )
            self._notify(t("notify.scrolling_unavailable").format(reason=reason))
            return
        self._open_smart_overlay(scrolling=True)

    def _open_smart_overlay(self, *, scrolling: bool) -> None:
        if self._uninstall_interaction_suspended:
            return
        operation_id = debug_log.new_operation_id("capture")
        self._current_debug_id = operation_id
        self._smart_debug_id = operation_id
        mode = "scrolling" if scrolling else "smart"
        _LOG.debug("op=%s capture_%s start", operation_id, mode)
        self._shelve_settings_dialog()
        blocklist = self._load_blocklist_or_abort()
        if blocklist is None:
            self._unshelve_settings_dialog()
            self._clear_smart_debug_id(operation_id)
            self._current_debug_id = None
            return
        allowlist = self._load_allowlist_or_abort()
        if allowlist is None:
            self._unshelve_settings_dialog()
            self._clear_smart_debug_id(operation_id)
            self._current_debug_id = None
            return
        self._allowlist = allowlist
        if self._is_wayland_platform():
            try:
                self._capture_wayland_interactive(blocklist, allowlist)
            finally:
                self._unshelve_settings_dialog()
                self._clear_smart_debug_id(operation_id)
                self._current_debug_id = None
            return
        # Snapshot the window list *before* showing the overlay so our own
        # window isn't a target. An empty/failed list is fine only when no
        # privacy policy needs window identity. If a blocklist/allowlist is in
        # force, failing to enumerate means we cannot prove what is safe to show
        # or redact, so fail closed rather than leak pixels on Wayland.
        try:
            windows = self._capturer.list_windows()
            _LOG.debug("op=%s capture_smart windows count=%s", operation_id, len(windows))
        except Exception as exc:
            _LOG.exception("op=%s capture_smart list_windows failed", operation_id)
            if blocklist or allowlist:
                self._window_policy_unavailable(exc)
                self._unshelve_settings_dialog()
                self._clear_smart_debug_id(operation_id)
                self._current_debug_id = None
                return
            windows = []
        # Resolve which on-screen windows are blocklisted up front, so the click
        # and hover-preview paths can refuse / skip them without re-querying.
        self._blocked_windows = {w.window_id: w for w in blocklist.blocked(windows)}
        # And, when the allowlist is on, which on-screen windows are *not* on it
        # — those are refused/skipped exactly like blocklisted ones.
        self._not_allowed_windows = (
            {w.window_id: w for w in windows if not allowlist.is_allowed(w)} if allowlist else {}
        )
        # Reuse the blocklisted windows we just resolved from this snapshot, so
        # _grab doesn't enumerate the whole window list a second time.
        screenshot = self._grab(blocklist, blocked=list(self._blocked_windows.values()))
        if screenshot is None:
            self._unshelve_settings_dialog()
            _LOG.debug("op=%s capture_smart abort screenshot_unavailable", operation_id)
            self._clear_smart_debug_id(operation_id)
            self._current_debug_id = None
            return
        geometry = self._app.primaryScreen().virtualGeometry()
        self._smart_screenshot = screenshot
        self._smart_geometry = geometry
        overlay = SmartOverlay(
            screenshot,
            geometry,
            windows,
            window_preview=None if scrolling else self._window_preview_image,
            hover_switch_delay_ms=self._config.hover_switch_delay_ms(),
            region_only=scrolling,
        )
        # Region captures carry the full screenshot along so the editor can
        # keep the crop adjustable (arrow-key nudging) until annotation starts.
        # Region and full-screen go through guarded handlers that refuse the
        # grab when the allowlist is on (only specific apps may be captured).
        if scrolling:
            # The overlay itself is region-only: clicks stay in place and its
            # persistent prompt explains that releasing a drag starts the run.
            overlay.region_selected.connect(self._scrolling_region_selected)
        else:
            overlay.region_selected.connect(self._smart_region_selected)
            overlay.window_selected.connect(self._capture_window_image)
            overlay.fullscreen_selected.connect(self._smart_fullscreen_selected)
        self._track(overlay)
        # After _track: _forget must drop the overlay from _windows first, so
        # the unshelve check doesn't still count the dying overlay as alive.
        overlay.destroyed.connect(self._unshelve_settings_dialog)
        overlay.destroyed.connect(self._clear_smart_capture_state)
        overlay.destroyed.connect(lambda: self._clear_smart_debug_id(operation_id))
        # present_overlay shows the overlay the way the platform needs: one
        # stay-on-top window spanning the virtual desktop (X11), or — on macOS,
        # where a single window sits under the menu bar and only covers one
        # display — one menu-bar-level window per screen sharing this overlay as
        # their brain. Wayland returned above and uses the portal picker instead.
        present_overlay(overlay, self._app)
        _LOG.debug(
            "op=%s capture_smart overlay shown blocked=%s not_allowed=%s",
            operation_id,
            len(self._blocked_windows),
            len(self._not_allowed_windows),
        )
        self._current_debug_id = None

    def _is_wayland_platform(self) -> bool:
        return self._app.platformName().lower().startswith("wayland")

    def _capture_wayland_interactive(
        self, blocklist: bl.Blocklist, allowlist: al.Allowlist
    ) -> None:
        """Use the compositor's portal picker for Wayland smart capture.

        Wayland does not expose other apps' window geometry to clients, so the
        in-process smart overlay cannot provide window highlight/direct picking
        there. The portal can: the compositor owns the UI and returns the user's
        chosen window, region, or screen as a still image.
        """
        operation_id = self._current_debug_id or self._smart_debug_id or "capture-unknown"
        _LOG.debug("op=%s capture_smart wayland_interactive start", operation_id)
        try:
            result, _target, _matched = headless.perform_interactive_capture(
                self._capturer, blocklist=blocklist, allowlist=allowlist, via="gui"
            )
        except headless.CaptureBlocked as exc:
            self._notify(str(exc))
            _LOG.debug("op=%s capture_smart wayland_interactive refused=%s", operation_id, exc)
            return
        except Exception as exc:
            self._notify(t("notify.capture_failed").format(error=exc))
            _LOG.exception("op=%s capture_smart wayland_interactive failed", operation_id)
            return
        image = result_to_qimage(result)
        _LOG.debug(
            "op=%s capture_smart wayland_interactive deliver size=%sx%s",
            operation_id,
            image.width(),
            image.height(),
        )
        # The Screenshot portal returns the selected pixels but not their
        # desktop rect. Passing a guessed origin would place the editor over the
        # wrong spot for window/region picks, so let the framed editor size
        # itself normally.
        self._deliver_capture(image)

    def _window_preview_image(self, window_id: int) -> QImage | None:
        """One window's un-occluded pixels for the overlay's hover preview.

        Called from the overlay's preview thread (capture_window only talks to
        the window server, which is thread-safe; QImage is GUI-thread-free).
        Returns None on failure — the overlay then keeps the frozen screenshot.
        """
        if window_id in self._blocked_windows or window_id in self._not_allowed_windows:
            # Never preview a blocklisted window's pixels, nor (when the allowlist
            # is on) a window that is not allowed to be captured.
            return None
        try:
            return result_to_qimage(self._capturer.capture_window(window_id))
        except Exception:
            _LOG.exception(
                "op=%s window preview failed window_id=%s",
                self._smart_debug_id,
                window_id,
            )
            return None

    def _smart_region_selected(self, image: QImage, rect: QRect) -> None:
        """Deliver a region crop, unless the allowlist forbids whole-screen grabs."""
        if self._allowlist:
            self._notify(t("notify.allowlist_whole_screen"))
            _LOG.debug("op=%s smart_region refused allowlist_enabled=true", self._smart_debug_id)
            return
        _LOG.debug("op=%s smart_region deliver rect=%s", self._smart_debug_id, rect)
        region = self._smart_backdrop_context(adjustable=self._config.region_adjust())
        self._deliver_capture(image, rect, region=region)

    def _smart_fullscreen_selected(self) -> None:
        """Deliver the full-screen shot, unless the allowlist forbids it."""
        if self._allowlist:
            self._notify(t("notify.allowlist_whole_screen"))
            _LOG.debug(
                "op=%s smart_fullscreen refused allowlist_enabled=true", self._smart_debug_id
            )
            return
        _LOG.debug("op=%s smart_fullscreen deliver", self._smart_debug_id)
        self._deliver_capture(self._smart_screenshot, self._smart_geometry)

    def _scrolling_needs_region(self, *args) -> None:
        """A window / full-screen pick can't frame a scroll; ask for a drag.

        Accepts ``*args`` so it can serve both overlay signals (``window_selected``
        carries id + rect, ``fullscreen_selected`` carries nothing)."""
        self._notify(t("notify.scrolling_needs_region"))

    def _scrolling_region_selected(self, image: QImage, rect: QRect) -> None:
        """Begin a long screenshot within the framed region: auto-scroll + stitch."""
        # A stale timer must not survive a second selection, but Settings should
        # remain hidden while the replacement session is prepared.
        self._end_scrolling(restore_settings=False)
        if self._allowlist:
            self._notify(t("notify.allowlist_whole_screen"))
            return
        if not getattr(self._capturer, "supports_repeated_region_capture", True):
            self._notify(
                t("notify.scrolling_unavailable").format(
                    reason="the capture backend only provides isolated still images"
                )
            )
            return
        blocklist = self._load_blocklist_or_abort()
        if blocklist is None:
            return
        try:
            scroller = get_scroller()
        except CapabilityUnsupported as exc:
            self._notify(t("notify.scrolling_unavailable").format(reason=exc.reason))
            return
        except Exception as exc:  # noqa: BLE001 - GUI boundary reports backend failures
            self._notify(t("notify.scrolling_unavailable").format(reason=exc))
            _LOG.exception("scrolling input initialization failed")
            return

        operation_id = self._smart_debug_id or debug_log.new_operation_id("scrolling")
        timer = QTimer(self)
        timer.setInterval(int(headless.SCROLL_INTERVAL_DEFAULT * 1000))
        timer.timeout.connect(self._scrolling_tick)
        status = ScrollingStatus(QRect(rect))
        self._scroll = _ScrollSession(
            timer=timer,
            accumulator=ScrollAccumulator(
                max_height=headless.SCROLL_MAX_HEIGHT_DEFAULT,
                settle=headless.SCROLL_SETTLE_DEFAULT,
                max_frames=headless.SCROLL_MAX_FRAMES_DEFAULT,
                start_frames=headless.SCROLL_AUTO_START_FRAMES_DEFAULT,
            ),
            scroller=scroller,
            status=status,
            rect=Rect(rect.x(), rect.y(), rect.width(), rect.height()),
            blocklist=blocklist,
            operation_id=operation_id,
        )
        status.stop_requested.connect(self._cancel_scrolling)
        self._app.installEventFilter(self)
        self._set_scrolling_action(True)
        status.present()
        # The first tick waits one interval, which also lets the overlay tear down
        # so the grab sees the page rather than the dimmed overlay.
        timer.start()

    def _scrolling_tick(self) -> None:
        """Sample, policy-check, stitch, and advance one live scroll frame."""
        session = self._scroll
        if session is None:
            return
        previous = self._current_debug_id
        self._current_debug_id = session.operation_id
        stitched = None
        status_hidden = False
        try:
            status_hidden = session.status.suspend_for_capture()
            if status_hidden:
                # Flush the hide to the window server before either enumerating
                # windows or grabbing pixels. User input processed here may
                # cancel the run, so re-check the session before continuing.
                self._app.processEvents()
                if self._scroll is not session:
                    return
            # Enforce the blocklist before raw pixels are grabbed. None means
            # enumeration failed and _blocked_on_screen already notified the user.
            blocked = self._blocked_on_screen(session.blocklist)
            if blocked is None:
                self._end_scrolling()
                return
            result = self._capturer.capture_region(session.rect)
            if blocked:
                result, _ = redact.redact_bounds(
                    result,
                    (session.rect.x, session.rect.y),
                    [window.bounds for window in blocked],
                )
            keep_going = session.accumulator.add(result_to_qimage(result))
            session.status.set_progress(session.accumulator.frame_count)
            self._set_scrolling_action(True)
            if keep_going:
                session.scroller.scroll(
                    -headless.SCROLL_CLICKS_DEFAULT,
                    at=session.rect.center(),
                )
                return
            stitched = session.accumulator.result()
        except NoScrollingDetected:
            _LOG.exception("op=%s scrolling did not start", session.operation_id)
            self._end_scrolling()
            self._notify(t("notify.scrolling_no_motion"))
            return
        except StitchError as exc:
            _LOG.exception("op=%s scrolling stitch failed", session.operation_id)
            self._end_scrolling()
            self._notify(t("notify.scrolling_failed").format(error=exc))
            return
        except Exception as exc:  # noqa: BLE001 - GUI boundary reports runtime failures
            _LOG.exception("op=%s scrolling capture failed", session.operation_id)
            self._end_scrolling()
            self._notify(t("notify.scrolling_failed").format(error=exc))
            return
        finally:
            # Keep an overlapping HUD hidden through synthetic wheel delivery as
            # well as the pixel grab. Otherwise a small/full-screen selection
            # could put the HUD under the pointer and consume the wheel event.
            if status_hidden and self._scroll is session:
                session.status.resume_after_capture()
            self._current_debug_id = previous

        self._end_scrolling()
        # A long image spans far past the framed region, so it opens in the plain
        # editor window (origin=None) rather than the region-aligned spotlight.
        self._current_debug_id = session.operation_id
        try:
            self._deliver_capture(stitched)
        finally:
            self._current_debug_id = previous

    def _cancel_scrolling(self, *, notify: bool = True) -> None:
        """Cancel an active long screenshot through the same teardown path."""
        if self._scroll is None:
            return
        self._end_scrolling()
        if notify:
            self._notify(t("notify.scrolling_cancelled"))

    def _end_scrolling(self, *, restore_settings: bool = True) -> None:
        """Tear down the timer and input driver, restoring pointer and UI state."""
        session = self._scroll
        if session is None:
            return
        self._scroll = None
        session.timer.stop()
        session.timer.deleteLater()
        self._app.removeEventFilter(self)
        session.status.close()
        session.status.deleteLater()
        try:
            close = getattr(session.scroller, "close", None)
            if callable(close):
                close()
        except Exception:  # noqa: BLE001 - teardown remains best effort
            _LOG.exception("op=%s scrolling input teardown failed", session.operation_id)
        self._set_scrolling_action(False)
        if restore_settings:
            self._unshelve_settings_dialog()

    def eventFilter(self, watched, event) -> bool:
        if (
            self._scroll is not None
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Escape
        ):
            self._cancel_scrolling()
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def _capture_window_image(self, window_id: int, origin: QRect) -> None:
        operation_id = self._smart_debug_id or debug_log.new_operation_id("capture")
        previous = self._current_debug_id
        self._current_debug_id = operation_id
        try:
            _LOG.debug(
                "op=%s capture_window start window_id=%s origin=%s",
                operation_id,
                window_id,
                origin,
            )
            blocked = self._blocked_windows.get(window_id)
            if blocked is not None:
                self._notify(t("notify.capture_blocked").format(app=blocked.owner))
                _LOG.debug(
                    "op=%s capture_window refused blocklisted window_id=%s owner=%s",
                    operation_id,
                    window_id,
                    blocked.owner,
                )
                return
            not_allowed = self._not_allowed_windows.get(window_id)
            if not_allowed is not None:
                self._notify(t("notify.capture_not_allowed").format(app=not_allowed.owner))
                _LOG.debug(
                    "op=%s capture_window refused not_allowed window_id=%s owner=%s",
                    operation_id,
                    window_id,
                    not_allowed.owner,
                )
                return
            try:
                result = self._capturer.capture_window(window_id)
            except Exception as exc:
                self._notify(t("notify.capture_failed").format(error=exc))
                _LOG.exception("op=%s capture_window failed window_id=%s", operation_id, window_id)
                return
            result = self._redact_window_overlaps(result, origin)
            _LOG.debug(
                "op=%s capture_window deliver window_id=%s size=%sx%s",
                operation_id,
                window_id,
                result.width,
                result.height,
            )
            self._deliver_capture(
                result_to_qimage(result),
                origin,
                region=self._smart_backdrop_context(adjustable=False),
            )
        finally:
            self._current_debug_id = previous

    def _smart_backdrop_context(self, *, adjustable: bool) -> RegionContext | None:
        if self._smart_screenshot is None or self._smart_geometry is None:
            return None
        return RegionContext(self._smart_screenshot, self._smart_geometry, adjustable=adjustable)

    def _redact_window_overlaps(self, result, target: QRect):
        """Hide windows stacked over the target whose pixels must not leak, when
        the grab may have read them off the framebuffer (no-compositor X11):
        blocklisted windows, and — when the allowlist is on — windows that are
        not allowed. Surface-accurate backends grab only the target's own pixels,
        so this is a no-op there — the capability is read defensively in case the
        backend predates it."""
        includes = getattr(self._capturer, "window_capture_includes_overlaps", None)
        if includes is None or not includes():
            return result
        from shotquill.capture.base import Rect

        target_rect = Rect(target.x(), target.y(), target.width(), target.height())
        # Union both per-session sets (keyed by id, so a window that is both
        # blocklisted and not-allowed is redacted once).
        hide = {**self._blocked_windows, **self._not_allowed_windows}
        overlaps = [
            w.bounds for w in hide.values() if redact.rect_intersects(target_rect, w.bounds)
        ]
        if not overlaps:
            return result
        result, _ = redact.redact_bounds(result, (target_rect.x, target_rect.y), overlaps)
        return result

    def _grab(self, blocklist: bl.Blocklist, blocked: list | None = None) -> QImage | None:
        # Resolve which on-screen windows are blocklisted *before* capturing, so
        # the backend can omit them from the grab itself (macOS ScreenCaptureKit):
        # the window is then simply absent — what was behind it shows through and
        # windows on top stay intact — instead of a solid block. Whatever the
        # backend can't omit (the legacy path) is painted out afterwards.
        #
        # ``blocked`` lets the smart overlay pass the on-screen blocklisted
        # windows it already enumerated, so we don't walk the whole window list
        # (a string of X11 round-trips) a second time.
        if blocked is None:
            blocked = self._blocked_on_screen(blocklist)
            if blocked is None:
                return None
        exclude_ids = frozenset(w.window_id for w in blocked)
        operation_id = self._current_debug_id or self._smart_debug_id or "capture-unknown"
        _LOG.debug(
            "op=%s grab fullscreen blocked=%s exclude_count=%s",
            operation_id,
            len(blocked),
            len(exclude_ids),
        )
        try:
            fast_grab = getattr(self._capturer, "capture_fullscreen_image", None)
            if not blocked and fast_grab is not None:
                # Nothing to redact (the common case — no blocklist hit on
                # screen): take the QImage straight from the backend, skipping
                # the QImage→bytes→QImage round-trip of CaptureResult. Those are
                # full-virtual-desktop copies, and allocating them is what
                # thrashes swap — and costs seconds — when memory is tight.
                # (getattr keeps duck-typed capturers without the fast path
                # working — they just fall through to the CaptureResult route.)
                image = fast_grab(exclude_ids)
                _LOG.debug(
                    "op=%s grab fullscreen complete backend=qimage_fast size=%sx%s",
                    operation_id,
                    image.width(),
                    image.height(),
                )
                return image
            result = self._capturer.capture_fullscreen(exclude_window_ids=exclude_ids)
        except Exception as exc:
            self._notify(t("notify.capture_failed").format(error=exc))
            _LOG.exception("op=%s grab fullscreen failed", operation_id)
            return None
        remaining = [w for w in blocked if w.window_id not in result.excluded_window_ids]
        if remaining:
            _LOG.debug("op=%s grab fullscreen redacting remaining=%s", operation_id, len(remaining))
            result, _ = redact.redact_bounds(
                result, (result.origin_x, result.origin_y), [w.bounds for w in remaining]
            )
        _LOG.debug(
            "op=%s grab fullscreen complete backend=capture_result size=%sx%s scale=%s",
            operation_id,
            result.width,
            result.height,
            result.scale,
        )
        return result_to_qimage(result)

    def _load_blocklist_or_abort(self) -> bl.Blocklist | None:
        """The blocklist for this capture, or ``None`` when it can't be read.

        Loaded once per capture (so Settings edits take effect) and then
        threaded through the whole capture, so window-marking and redaction
        share one snapshot — no second read can race in a file that's
        corrupted mid-capture.

        On a present-but-unreadable list we notify and return ``None`` so the
        caller fails closed: the user opted into protection that is now broken,
        and silently capturing would hand a blocked app to the editor or
        clipboard — the exact leak the blocklist exists to prevent. The headless
        (CLI/MCP) surface fails closed here too. A missing or valid list (the
        common case) returns the list and the capture proceeds."""
        try:
            return bl.load()
        except bl.BlocklistError as exc:
            self._notify(t("notify.blocklist_unreadable").format(error=exc))
            _LOG.exception("blocklist load failed")
            return None

    def _load_allowlist_or_abort(self) -> al.Allowlist | None:
        """The allowlist for this capture, or ``None`` when it can't be read.

        A missing file is the disabled empty allowlist (the common case — capture
        proceeds normally). A present-but-unreadable file fails *closed* exactly
        like the blocklist: the user opted into a restriction that is now broken,
        so we notify and abort rather than capture something they meant to keep
        out. Loaded once per capture so Settings edits take effect immediately."""
        try:
            return al.load()
        except al.AllowlistError as exc:
            self._notify(t("notify.allowlist_unreadable").format(error=exc))
            _LOG.exception("allowlist load failed")
            return None

    def _window_policy_unavailable(self, error: Exception) -> None:
        self._notify(t("notify.window_policy_unavailable").format(error=error))

    def _blocked_on_screen(self, blocklist: bl.Blocklist) -> list | None:
        """Blocklisted windows currently on screen, so a blocked app never reaches
        the editor, clipboard, or a saved file — the same protection the CLI/MCP
        get, on the human path.

        Empty when nothing is blocked. ``None`` means an active blocklist could
        not be enforced because the backend cannot enumerate windows."""
        if not blocklist:
            return []
        try:
            windows = self._capturer.list_windows()
        except Exception as exc:
            _LOG.exception("blocked_on_screen list_windows failed")
            self._window_policy_unavailable(exc)
            return None
        return blocklist.blocked(windows)

    def _notify(self, message: str) -> None:
        self._tray.showMessage("ShotQuill", message, QSystemTrayIcon.MessageIcon.Critical)

    def _deliver_capture(
        self,
        image: QImage,
        origin: QRect | None = None,
        region: RegionContext | None = None,
    ) -> None:
        # Single exit for every capture mode. Flash/sound feedback fires either
        # way; then auto-output (if enabled) saves/copies the raw shot hands-free
        # and skips the editor. With both auto toggles off, the editor opens —
        # placed over ``origin`` (the shot's on-screen rect) when known, with
        # ``region`` keeping a region capture's crop arrow-key adjustable there.
        operation_id = self._current_debug_id or self._smart_debug_id or "capture-unknown"
        _LOG.debug(
            "op=%s deliver_capture size=%sx%s origin=%s region=%s",
            operation_id,
            image.width(),
            image.height(),
            origin,
            bool(region),
        )
        self._signal_capture()
        if self._auto_output(image):
            return
        self._open_editor(image, origin, region)

    def _auto_output(self, image: QImage) -> bool:
        """Save and/or copy the raw shot per config; return True if it handled it.

        A failed auto-save returns False so the editor opens as a fallback —
        otherwise the shot would be lost entirely (a notification is no place
        to keep an image). Any auto-copy has already run by then, so the
        clipboard is intact either way.
        """
        save = self._config.auto_save_after_capture()
        copy = self._config.auto_copy_after_capture()
        if not (save or copy):
            return False
        operation_id = self._current_debug_id or self._smart_debug_id or "capture-unknown"
        _LOG.debug(
            "op=%s auto_output save=%s copy=%s size=%sx%s",
            operation_id,
            save,
            copy,
            image.width(),
            image.height(),
        )
        if copy:
            from shotquill.output.clipboard import copy_qimage

            copy_qimage(image)
        if save:
            from shotquill.output.saver import save_qimage

            try:
                save_qimage(image, self._config.save_dir(), self._config.image_format())
            except OSError as exc:
                self._notify(t("notify.save_failed").format(error=exc))
                _LOG.exception("op=%s auto_save failed", operation_id)
                return False
        return True

    def _open_editor(
        self,
        image: QImage,
        origin: QRect | None = None,
        region: RegionContext | None = None,
    ) -> None:
        if region is not None and not self._config.region_adjust():
            region = region._replace(adjustable=False)
        operation_id = self._current_debug_id or self._smart_debug_id or "capture-unknown"
        _LOG.debug(
            "op=%s open_editor size=%sx%s origin=%s region=%s",
            operation_id,
            image.width(),
            image.height(),
            origin,
            bool(region),
        )
        editor = self._make_editor(image, origin, region)
        editor.pin_requested.connect(self._pin_image)
        self._track(editor)
        editor.show()
        editor.raise_()
        editor.activateWindow()

    def _make_editor(self, image: QImage, origin: QRect | None, region: RegionContext | None):
        """Pick the editor shell: the unified full-screen spotlight surface when
        spotlight mode is on and the shot sits on a single screen; otherwise the
        framed window (titled mode, a shot spanning screens — e.g. a fullscreen
        capture — which one surface can't cover, or Wayland).

        The spotlight surface must sit exactly on the selection's screen. On
        Wayland the compositor ignores a set window position (it tiles/places
        top-levels itself), so the surface couldn't align with the shot and the
        crop handles would be off — fall back to the framed window there. macOS
        and X11 honour the geometry, so they get the surface."""
        from shotquill.ui.spotlight import SpotlightSurface

        wayland = self._app.platformName().lower().startswith("wayland")
        if self._config.editor_backdrop() and origin is not None and not wayland:
            screen = self._app.screenAt(origin.center())
            if screen is not None and screen.geometry().contains(origin):
                return SpotlightSurface(image, self._config, origin, region)
        return EditorWindow(image, self._config, origin, region)

    def _pin_image(self, image: QImage, origin: QRect | None = None) -> None:
        pinned = PinnedWindow(image, origin, self._config)
        self._track(pinned)
        pinned.show()
        pinned.raise_()

    def _signal_capture(self) -> None:
        """Flash the screen and/or play a sound to confirm a shot was taken."""
        self._feedback.trigger(
            self._app.primaryScreen().virtualGeometry(),
            flash=self._config.flash_on_capture(),
            sound=self._config.sound_on_capture(),
        )

    def _sync_autostart(self) -> None:
        """Make the on-disk login entry match the saved preference."""
        try:
            self._autostart.set_enabled(self._config.autostart())
        except OSError:
            _LOG.exception("autostart sync failed")
            pass  # non-fatal: launch-at-login is a convenience, not core function

    def _open_save_folder(self) -> None:
        """Reveal the configured save directory in the system file manager.

        The folder is created on demand the same way the saver does it, so the
        menu item works even before the first capture has written anything —
        otherwise the opener would fail on a path that doesn't exist yet. macOS
        uses ``open``; Linux uses ``xdg-open`` (the freedesktop opener); Windows
        uses ``os.startfile``, which hands the path to Explorer directly.
        """
        directory = Path(self._config.save_dir()).expanduser()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            if sys.platform.startswith("win"):
                # ``os.startfile`` is Windows-only and returns immediately once
                # Explorer has been asked to open the folder — there is no
                # equivalent ``xdg-open``/``open`` binary to shell out to.
                os.startfile(str(directory))  # noqa: S606 - opening a user dir in Explorer
                return
            # ``open``/``xdg-open`` normally fork the file manager and return at
            # once; the timeout keeps a misconfigured opener from hanging this
            # (main-thread) menu action forever. TimeoutExpired is a
            # SubprocessError, so the existing handler reports it.
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.run([opener, str(directory)], check=True, timeout=20)
        except (OSError, subprocess.SubprocessError) as exc:
            self._notify(t("notify.open_folder_failed").format(error=exc))
            _LOG.exception("open save folder failed")

    def _open_settings(self) -> None:
        # Modeless on purpose. exec() would make the dialog application-modal,
        # which Qt elevates to a macOS panel window level — and window levels
        # are global, so the dialog would float above *other apps'* windows
        # even with ShotQuill in the background. There is no main window for
        # modality to protect anyway; re-triggering the menu item while the
        # dialog is open just brings it back to front.
        if self._settings_dialog is not None:
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        dialog = SettingsDialog(self._config)
        dialog.accepted.connect(self._apply_settings)
        dialog.uninstall_requested.connect(self._request_uninstall)
        dialog.finished.connect(self._forget_settings_dialog)
        self._settings_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _request_uninstall(self) -> None:
        """Route uninstall through Brew or the fixed direct-PKG helper."""
        if self._uninstall_probe_running or self._uninstall_process is not None:
            return
        self._uninstall_probe_running = True
        self._uninstall_probe_generation += 1
        generation = self._uninstall_probe_generation
        self._show_uninstall_progress(t("uninstall.inspecting"), cancelable=True)
        thread = threading.Thread(
            target=self._probe_uninstall,
            args=(generation,),
            name="shotquill-uninstall-probe",
            daemon=True,
        )
        self._uninstall_probe_thread = thread
        thread.start()

    def _probe_uninstall(self, generation: int) -> None:
        try:
            plan = uninstall.prepare_uninstall_plan()
        except Exception as exc:  # noqa: BLE001 - GUI boundary reports probe failures
            _LOG.exception("uninstall inspection failed")
            self._uninstall_probe_bridge.finished.emit(generation, None, exc)
            return
        self._uninstall_probe_bridge.finished.emit(generation, plan, None)

    @Slot(int, object, object)
    def _uninstall_probe_finished(
        self,
        generation: int,
        plan: object,
        error: object,
    ) -> None:
        if generation != self._uninstall_probe_generation or not self._uninstall_probe_running:
            return
        self._uninstall_probe_running = False
        self._uninstall_probe_thread = None
        self._clear_uninstall_progress()
        if error is not None:
            QMessageBox.critical(
                self._settings_dialog,
                t("uninstall.title"),
                t("uninstall.start_failed").format(error=error),
            )
            return
        if not isinstance(plan, uninstall.UninstallPlan):
            QMessageBox.critical(
                self._settings_dialog,
                t("uninstall.title"),
                t("uninstall.start_failed").format(error="invalid uninstall plan"),
            )
            return
        self._present_uninstall_plan(plan)

    def _present_uninstall_plan(self, plan: uninstall.UninstallPlan) -> None:
        """Show the channel-specific action after background inspection."""
        preview = uninstall.format_uninstall_plan(
            plan,
            language=self._config.language(),
        )

        if not plan.can_execute:
            QMessageBox.warning(
                self._settings_dialog,
                t("uninstall.title"),
                t("uninstall.unavailable").format(plan=preview),
            )
            return
        if plan.channel is uninstall.InstallChannel.HOMEBREW:
            if plan.brew_command is None:  # Defensive: executable plans always include it.
                QMessageBox.warning(
                    self._settings_dialog,
                    t("uninstall.title"),
                    t("uninstall.unavailable").format(plan=preview),
                )
                return
            QMessageBox.information(
                self._settings_dialog,
                t("uninstall.title"),
                t("uninstall.brew").format(
                    command=shlex.join(plan.brew_command),
                    plan=preview,
                ),
            )
            return

        if not self._confirm_uninstall(preview):
            return
        self._start_direct_uninstall(plan)

    def _confirm_uninstall(self, preview: str) -> bool:
        """Ask for explicit confirmation with Cancel as the safe default."""
        dialog = QMessageBox(self._settings_dialog)
        dialog.setWindowTitle(t("uninstall.title"))
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setText(t("uninstall.confirm").format(plan=preview))
        uninstall_button = dialog.addButton(
            t("uninstall.action"),
            QMessageBox.ButtonRole.DestructiveRole,
        )
        cancel_button = dialog.addButton(
            t("uninstall.cancel"),
            QMessageBox.ButtonRole.RejectRole,
        )
        dialog.setDefaultButton(cancel_button)
        dialog.setEscapeButton(cancel_button)
        dialog.exec()
        return dialog.clickedButton() is uninstall_button

    def _show_uninstall_progress(self, message: str, *, cancelable: bool = False) -> None:
        if self._settings_dialog is None:
            return
        self._clear_uninstall_progress()
        cancel_text = t("uninstall.cancel") if cancelable else ""
        progress = QProgressDialog(message, cancel_text, 0, 0, self._settings_dialog)
        progress.setWindowTitle(t("uninstall.title"))
        if cancelable:
            progress.canceled.connect(self._cancel_uninstall_probe)
        else:
            progress.setCancelButton(None)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        self._uninstall_progress = progress
        progress.show()

    def _cancel_uninstall_probe(self) -> None:
        """Hide a slow read-only probe and ignore its eventual result."""
        if not self._uninstall_probe_running:
            return
        self._uninstall_probe_generation += 1
        self._uninstall_probe_running = False
        self._uninstall_probe_thread = None
        self._clear_uninstall_progress()

    def _clear_uninstall_progress(self) -> None:
        if self._uninstall_progress is None:
            return
        self._uninstall_progress.close()
        self._uninstall_progress.deleteLater()
        self._uninstall_progress = None

    def _start_direct_uninstall(self, plan: uninstall.UninstallPlan) -> None:
        """Start the protected post-exit coordinator without blocking Qt."""
        try:
            argv = uninstall.gui_coordinator_argv(
                plan,
                parent_pid=os.getpid(),
                language=self._config.language(),
            )
        except (OSError, ValueError) as exc:
            _LOG.exception("uninstall helper launch failed")
            QMessageBox.critical(
                self._settings_dialog,
                t("uninstall.title"),
                t("uninstall.start_failed").format(error=exc),
            )
            return

        process = QProcess(self)
        process.setProgram(argv[0])
        process.setArguments(list(argv[1:]))
        process.finished.connect(self._uninstall_finished)
        process.errorOccurred.connect(self._uninstall_process_error)
        launch_timer = QTimer(self)
        launch_timer.setSingleShot(True)
        launch_timer.timeout.connect(self._uninstall_launch_timed_out)
        self._uninstall_process = process
        self._uninstall_launch_timer = launch_timer
        self._uninstall_launcher_timed_out = False
        self._uninstall_interaction_suspended = True
        self._hotkeys.clear()
        for action in self._capture_actions:
            action.setEnabled(False)
        if self._quit_action is not None:
            self._quit_action.setEnabled(False)
        self._show_uninstall_progress(t("uninstall.preparing"))
        launch_timer.start(_UNINSTALL_LAUNCH_TIMEOUT_MS)
        process.start()

    def _stop_uninstall_timer(self, timer: QTimer | None) -> None:
        if timer is None:
            return
        timer.stop()
        timer.deleteLater()

    def _release_uninstall_process(
        self,
        *,
        restore_interaction: bool = True,
        delete_process: bool = True,
    ) -> None:
        process = self._uninstall_process
        self._uninstall_process = None
        launch_timer = self._uninstall_launch_timer
        self._uninstall_launch_timer = None
        kill_timer = self._uninstall_kill_timer
        self._uninstall_kill_timer = None
        self._stop_uninstall_timer(launch_timer)
        self._stop_uninstall_timer(kill_timer)
        self._uninstall_launcher_timed_out = False
        self._clear_uninstall_progress()
        if restore_interaction:
            self._uninstall_interaction_suspended = False
            for action in self._capture_actions:
                action.setEnabled(True)
            self._apply_permission_gated_hotkeys()
            if self._quit_action is not None:
                self._quit_action.setEnabled(True)
        if process is not None and delete_process:
            process.deleteLater()

    def _uninstall_launch_timed_out(self) -> None:
        """Ask the launcher to exit so its EXIT trap can clean its coordinator."""
        process = self._uninstall_process
        if process is None or self._uninstall_launcher_timed_out:
            return
        self._uninstall_launcher_timed_out = True
        self._stop_uninstall_timer(self._uninstall_launch_timer)
        self._uninstall_launch_timer = None
        _LOG.error("uninstall coordinator launcher timed out")
        process.terminate()

        kill_timer = QTimer(self)
        kill_timer.setSingleShot(True)
        kill_timer.timeout.connect(self._kill_timed_out_uninstall_launcher)
        self._uninstall_kill_timer = kill_timer
        kill_timer.start(_UNINSTALL_TERM_GRACE_MS)

    def _kill_timed_out_uninstall_launcher(self) -> None:
        """Force a stuck launcher down after allowing its TERM cleanup to run."""
        process = self._uninstall_process
        if process is None or not self._uninstall_launcher_timed_out:
            return
        process.finished.connect(lambda *_args: process.deleteLater())
        process.kill()
        if self._uninstall_process is not process:
            return
        self._release_uninstall_process(delete_process=False)
        QMessageBox.critical(
            self._settings_dialog,
            t("uninstall.title"),
            t("uninstall.start_failed").format(error=_UNINSTALL_TIMEOUT_DETAIL),
        )

    def _uninstall_process_error(self, error: QProcess.ProcessError) -> None:
        if error != QProcess.ProcessError.FailedToStart or self._uninstall_process is None:
            return
        message = self._uninstall_process.errorString()
        self._release_uninstall_process()
        _LOG.error("uninstall authorization process failed to start: %s", message)
        QMessageBox.critical(
            self._settings_dialog,
            t("uninstall.title"),
            t("uninstall.start_failed").format(error=message),
        )

    def _uninstall_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        process = self._uninstall_process
        if process is None:
            return
        stderr = bytes(process.readAllStandardError()).decode("utf-8", errors="replace")
        timed_out = self._uninstall_launcher_timed_out
        succeeded = (
            not timed_out and exit_status == QProcess.ExitStatus.NormalExit and exit_code == 0
        )
        self._release_uninstall_process(restore_interaction=not succeeded)

        if not succeeded:
            detail = _UNINSTALL_TIMEOUT_DETAIL if timed_out else stderr.strip() or str(exit_code)
            QMessageBox.critical(
                self._settings_dialog,
                t("uninstall.title"),
                t("uninstall.start_failed").format(error=detail),
            )
            return

        self._app.quit()

    def _apply_settings(self) -> None:
        debug_log.configure(self._config)
        _LOG.debug("settings applied debug_mode=%s", self._config.debug_mode())
        set_language(self._config.language())
        self._capturer.include_cursor = self._config.include_cursor()
        self._apply_permission_gated_hotkeys()
        self._sync_autostart()
        self._rebuild_menu()
        # Editors resolve their finish keys at creation; push the new
        # bindings into any that are still open.
        for window in self._windows:
            if isinstance(window, EditorCoreMixin):  # both editor shells
                window.reload_finish_keys()

    def _forget_settings_dialog(self) -> None:
        if self._settings_dialog is not None:
            self._settings_dialog.deleteLater()
            self._settings_dialog = None

    def _show_about(self) -> None:
        body = (
            f"<b>ShotQuill</b> {__version__}<br><br>"
            f"{t('about.body')}<br>"
            "© 2026 wardmos · Apache-2.0<br>"
            "Built with Qt (PySide6, LGPLv3)."
        )
        QMessageBox.about(None, t("menu.about"), body)

    def _track(self, window: object) -> None:
        self._windows.append(window)
        window.destroyed.connect(lambda: self._forget(window))

    def _forget(self, window: object) -> None:
        if window in self._windows:
            self._windows.remove(window)

    def _clear_smart_debug_id(self, operation_id: str) -> None:
        if self._smart_debug_id == operation_id:
            self._smart_debug_id = None

    def _clear_smart_capture_state(self) -> None:
        self._smart_screenshot = None
        self._smart_geometry = None

    def shutdown(self) -> None:
        self._end_scrolling(restore_settings=False)  # stop any in-flight timer
        self._hotkeys.stop()


def run() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("ShotQuill")
    app.setDesktopFileName(LINUX_GUI_DESKTOP_FILE_NAME)
    app.setQuitOnLastWindowClosed(False)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        from shotquill.i18n import tray_unavailable_body_key

        title = t("tray.unavailable_title")
        body = t(tray_unavailable_body_key())
        # A user double-clicking the desktop entry sees nothing if we only
        # print and exit — show a proper dialog so the failure isn't silent,
        # and keep the stderr line for the CLI/launcher path.
        print(f"{title}: {body}", file=sys.stderr)
        QMessageBox.critical(None, title, body)
        return 1

    instance = ShotquillApp(app)
    exit_code = app.exec()
    instance.shutdown()
    return exit_code
