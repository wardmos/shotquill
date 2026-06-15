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
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QRect, Qt, Signal, Slot
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from shotquill import __version__, permissions, redact
from shotquill import allowlist as al
from shotquill import blocklist as bl
from shotquill.autostart import get_manager as get_autostart_manager
from shotquill.config import Config, human_readable_hotkey
from shotquill.headless import get_capturer
from shotquill.hotkeys import get_manager as get_hotkey_manager
from shotquill.hotkeys.base import HotkeyUnavailable
from shotquill.i18n import set_language, t
from shotquill.imaging import result_to_qimage
from shotquill.ui.editor import EditorWindow, RegionContext
from shotquill.ui.feedback import CaptureFeedback
from shotquill.ui.pinned import PinnedWindow
from shotquill.ui.settings import SettingsDialog
from shotquill.ui.smart_overlay import SmartOverlay, present_overlay


def _build_icon() -> QIcon:
    """Build the menu-bar / tray mark.

    On macOS we render a *template* image: monochrome, only the alpha channel
    matters, and macOS tints the opaque pixels to match the menu bar (white on
    dark, dark on light) like its own status items — so the tile is solid black
    with the "S" knocked out and flagged as a mask. Other desktops (Linux/X11
    tray) don't tint a mask, which would leave a black tile with a transparent
    glyph — invisible on dark panels — so there we draw a self-contained icon:
    a black tile with the "S" painted in white. The colored Launchpad icon is a
    separate ``.icns`` and is unaffected.

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
    """Render the rounded-square "S" tray glyph at ``size``×``size`` pixels.

    The proportions (corner radius, tile padding, glyph height) are all derived
    from ``size`` so smaller pixmaps stay legible — at 16px the glyph dominates
    the tile, at 64px there is breathing room. macOS gets the "S" knocked out of
    a black mask; everywhere else the glyph is painted white so the tile reads
    on dark panels without relying on the desktop to tint it.
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("black"))
    padding = max(1, round(size * 6 / 64))
    radius = max(2, round(size * 14 / 64))
    tile = size - 2 * padding
    painter.drawRoundedRect(padding, padding, tile, tile, radius, radius)
    if is_mac:
        painter.setCompositionMode(QPainter.CompositionMode_DestinationOut)
        glyph_color = QColor("black")
    else:
        glyph_color = QColor("white")
    font = QFont()
    font.setBold(True)
    font.setPixelSize(max(8, round(size * 46 / 64)))
    painter.setFont(font)
    painter.setPen(glyph_color)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "S")
    painter.end()
    return pixmap


class _HotkeyBridge(QObject):
    """Marshals hotkey events from the pynput listener thread onto the Qt main thread."""

    smart_requested = Signal()
    fullscreen_requested = Signal()


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

        self._bridge = _HotkeyBridge()
        # Hotkeys are emitted from pynput's listener thread. Force queued delivery
        # so capture code always runs on Qt's GUI thread, where widgets/windows are safe.
        self._bridge.smart_requested.connect(
            self._capture_smart, Qt.ConnectionType.QueuedConnection
        )
        self._bridge.fullscreen_requested.connect(
            self._capture_fullscreen, Qt.ConnectionType.QueuedConnection
        )

        self._tray = QSystemTrayIcon(_build_icon(), self._app)
        self._tray.setToolTip("ShotQuill")
        self._rebuild_menu()
        self._tray.show()
        self._apply_hotkeys()

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
        open_folder = QAction(t("menu.open_folder"), menu)
        open_folder.triggered.connect(self._open_save_folder)
        settings = QAction(t("menu.settings"), menu)
        settings.triggered.connect(self._open_settings)
        about = QAction(t("menu.about"), menu)
        about.triggered.connect(self._show_about)
        quit_action = QAction(t("menu.quit"), menu)
        quit_action.triggered.connect(self._app.quit)

        menu.addAction(smart)
        menu.addAction(fullscreen)
        menu.addSeparator()
        menu.addAction(open_folder)
        menu.addAction(settings)
        menu.addAction(about)
        menu.addSeparator()
        menu.addAction(quit_action)

        self._tray.setContextMenu(menu)
        self._menu = menu  # keep a reference

    def _apply_hotkeys(self) -> None:
        # Note: no stop() here. Restarting the pynput listener while Qt runs
        # crashes the process (SIGTRAP on the listener thread), so the manager
        # keeps one listener alive and start() just swaps in the new bindings.
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
                self._hotkeys.register(self._config.hotkey(action), emit, description=label)
        try:
            self._hotkeys.start()
        except HotkeyUnavailable as exc:
            # The session refuses global grabs (e.g. Wayland). Nothing to grant
            # — surface the reason once and keep the tray menu working.
            self._notify(t("notify.hotkeys_unavailable").format(reason=exc.reason))
        except PermissionError:
            self._notify(t("notify.hotkeys_need_input_monitoring"))
            # The deep-link is macOS-specific (an x-apple-systempreferences URL
            # opened via `open`); skip it elsewhere where it would be a no-op.
            if sys.platform == "darwin":
                permissions.open_input_monitoring_pane()

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
        if any(isinstance(window, SmartOverlay) for window in self._windows):
            return  # another capture overlay is still up; its close retries
        self._settings_shelved = False
        if self._settings_dialog is not None:
            self._settings_dialog.show()

    @Slot()
    def _capture_fullscreen(self) -> None:
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
            return
        try:
            screenshot = self._grab(blocklist)
        finally:
            self._unshelve_settings_dialog()
        if screenshot is not None:
            self._deliver_capture(screenshot, self._app.primaryScreen().virtualGeometry())

    @Slot()
    def _capture_smart(self) -> None:
        self._shelve_settings_dialog()
        blocklist = self._load_blocklist_or_abort()
        if blocklist is None:
            self._unshelve_settings_dialog()
            return
        allowlist = self._load_allowlist_or_abort()
        if allowlist is None:
            self._unshelve_settings_dialog()
            return
        self._allowlist = allowlist
        # Snapshot the window list *before* showing the overlay so our own
        # window isn't a target. An empty/failed list is fine — the overlay
        # then only offers full-screen and region modes.
        try:
            windows = self._capturer.list_windows()
        except Exception:
            windows = []
        # Resolve which on-screen windows are blocklisted up front, so the click
        # and hover-preview paths can refuse / skip them without re-querying.
        self._blocked_windows = {w.window_id: w for w in blocklist.blocked(windows)}
        # And, when the allowlist is on, which on-screen windows are *not* on it
        # — those are refused/skipped exactly like blocklisted ones.
        self._not_allowed_windows = (
            {w.window_id: w for w in windows if not allowlist.is_allowed(w)} if allowlist else {}
        )
        screenshot = self._grab(blocklist)
        if screenshot is None:
            self._unshelve_settings_dialog()
            return
        geometry = self._app.primaryScreen().virtualGeometry()
        self._smart_screenshot = screenshot
        self._smart_geometry = geometry
        overlay = SmartOverlay(
            screenshot,
            geometry,
            windows,
            window_preview=self._window_preview_image,
            hover_switch_delay_ms=self._config.hover_switch_delay_ms(),
        )
        # Region captures carry the full screenshot along so the editor can
        # keep the crop adjustable (arrow-key nudging) until annotation starts.
        # Region and full-screen go through guarded handlers that refuse the
        # grab when the allowlist is on (only specific apps may be captured).
        overlay.region_selected.connect(self._smart_region_selected)
        overlay.window_selected.connect(self._capture_window_image)
        overlay.fullscreen_selected.connect(self._smart_fullscreen_selected)
        self._track(overlay)
        # After _track: _forget must drop the overlay from _windows first, so
        # the unshelve check doesn't still count the dying overlay as alive.
        overlay.destroyed.connect(self._unshelve_settings_dialog)
        # present_overlay shows the overlay the way the platform needs: one
        # stay-on-top window spanning the virtual desktop (X11), Wayland
        # fullscreen, or — on macOS, where a single window sits under the menu
        # bar and only covers one display — one menu-bar-level window per screen
        # sharing this overlay as their brain.
        present_overlay(overlay, self._app)

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
            return None

    def _smart_region_selected(self, image: QImage, rect: QRect) -> None:
        """Deliver a region crop, unless the allowlist forbids whole-screen grabs."""
        if self._allowlist:
            self._notify(t("notify.allowlist_whole_screen"))
            return
        self._deliver_capture(
            image, rect, region=RegionContext(self._smart_screenshot, self._smart_geometry)
        )

    def _smart_fullscreen_selected(self) -> None:
        """Deliver the full-screen shot, unless the allowlist forbids it."""
        if self._allowlist:
            self._notify(t("notify.allowlist_whole_screen"))
            return
        self._deliver_capture(self._smart_screenshot, self._smart_geometry)

    def _capture_window_image(self, window_id: int, origin: QRect) -> None:
        blocked = self._blocked_windows.get(window_id)
        if blocked is not None:
            self._notify(t("notify.capture_blocked").format(app=blocked.owner))
            return
        not_allowed = self._not_allowed_windows.get(window_id)
        if not_allowed is not None:
            self._notify(t("notify.capture_not_allowed").format(app=not_allowed.owner))
            return
        try:
            result = self._capturer.capture_window(window_id)
        except Exception as exc:
            self._notify(t("notify.capture_failed").format(error=exc))
            return
        result = self._redact_window_overlaps(result, origin)
        self._deliver_capture(result_to_qimage(result), origin)

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

    def _grab(self, blocklist: bl.Blocklist) -> QImage | None:
        # Resolve which on-screen windows are blocklisted *before* capturing, so
        # the backend can omit them from the grab itself (macOS ScreenCaptureKit):
        # the window is then simply absent — what was behind it shows through and
        # windows on top stay intact — instead of a solid block. Whatever the
        # backend can't omit (the legacy path) is painted out afterwards.
        blocked = self._blocked_on_screen(blocklist)
        exclude_ids = frozenset(w.window_id for w in blocked)
        try:
            result = self._capturer.capture_fullscreen(exclude_window_ids=exclude_ids)
        except Exception as exc:
            self._notify(t("notify.capture_failed").format(error=exc))
            return None
        remaining = [w for w in blocked if w.window_id not in result.excluded_window_ids]
        if remaining:
            result, _ = redact.redact_bounds(
                result, (result.origin_x, result.origin_y), [w.bounds for w in remaining]
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
            return None

    def _blocked_on_screen(self, blocklist: bl.Blocklist) -> list:
        """Blocklisted windows currently on screen, so a blocked app never reaches
        the editor, clipboard, or a saved file — the same protection the CLI/MCP
        get, on the human path.

        Empty when nothing is blocked or the windows can't be enumerated (macOS
        always can; a backend that can't enumerate also can't redact)."""
        if not blocklist:
            return []
        try:
            windows = self._capturer.list_windows()
        except Exception:
            return []
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
        if copy:
            from shotquill.output.clipboard import copy_qimage

            copy_qimage(image)
        if save:
            from shotquill.output.saver import save_qimage

            try:
                save_qimage(image, self._config.save_dir(), self._config.image_format())
            except OSError as exc:
                self._notify(t("notify.save_failed").format(error=exc))
                return False
        return True

    def _open_editor(
        self,
        image: QImage,
        origin: QRect | None = None,
        region: RegionContext | None = None,
    ) -> None:
        if not self._config.region_adjust():
            region = None  # the user turned crop adjustment off in Settings
        editor = EditorWindow(image, self._config, origin, region)
        editor.pin_requested.connect(self._pin_image)
        self._track(editor)
        editor.show()
        editor.raise_()
        editor.activateWindow()

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
        dialog.finished.connect(self._forget_settings_dialog)
        self._settings_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _apply_settings(self) -> None:
        set_language(self._config.language())
        self._capturer.include_cursor = self._config.include_cursor()
        self._apply_hotkeys()
        self._sync_autostart()
        self._rebuild_menu()
        # Editors resolve their finish keys at creation; push the new
        # bindings into any that are still open.
        for window in self._windows:
            if isinstance(window, EditorWindow):
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

    def shutdown(self) -> None:
        self._hotkeys.stop()


def run() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("ShotQuill")
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
