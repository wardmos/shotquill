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

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QRect, Qt, Signal, Slot
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from shotquill import __version__, permissions, redact
from shotquill import blocklist as bl
from shotquill.autostart.macos import MacAutostartManager
from shotquill.capture.macos import MacScreenCapturer
from shotquill.config import Config, human_readable_hotkey
from shotquill.hotkeys.macos import MacHotkeyManager
from shotquill.i18n import set_language, t
from shotquill.imaging import result_to_qimage
from shotquill.ui.editor import EditorWindow, RegionContext
from shotquill.ui.feedback import CaptureFeedback
from shotquill.ui.pinned import PinnedWindow
from shotquill.ui.settings import SettingsDialog
from shotquill.ui.smart_overlay import SmartOverlay


def _build_icon() -> QIcon:
    """Build the menu-bar mark as a macOS *template* image.

    Template images are monochrome: only the alpha channel matters, and macOS
    tints the opaque pixels to match the menu bar (white on dark, dark on light)
    like its own status items. We draw the brand tile in solid black with the
    "S" knocked out, then flag it as a mask. The colored Launchpad icon is a
    separate ``.icns`` and is unaffected.
    """
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("black"))
    painter.drawRoundedRect(6, 6, 52, 52, 14, 14)
    # Erase the "S" from the tile so it shows the menu-bar colour through.
    # Size the glyph in pixels relative to the 64px canvas so it fills most of
    # the tile — a small point size reads as a tiny letter once macOS scales the
    # whole tile down to menu-bar height.
    painter.setCompositionMode(QPainter.CompositionMode_DestinationOut)
    font = QFont()
    font.setBold(True)
    font.setPixelSize(46)
    painter.setFont(font)
    painter.setPen(QColor("black"))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "S")
    painter.end()

    icon = QIcon(pixmap)
    icon.setIsMask(True)  # tell macOS to render it as a template image
    return icon


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

        self._capturer = MacScreenCapturer(include_cursor=self._config.include_cursor())
        self._hotkeys = MacHotkeyManager()
        self._feedback = CaptureFeedback()
        self._autostart = MacAutostartManager()
        self._sync_autostart()
        self._windows: list[object] = []  # keep overlays/editors alive
        self._settings_dialog: SettingsDialog | None = None
        self._settings_shelved = False  # Settings hidden while a capture runs
        # Blocklisted windows on screen for the current smart-capture session
        # (id → window); refused on click, skipped in the hover preview.
        self._blocked_windows: dict[int, object] = {}

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
        actions = (
            ("smart_capture", self._bridge.smart_requested.emit),
            ("fullscreen_capture", self._bridge.fullscreen_requested.emit),
        )
        for action, emit in actions:
            if self._config.hotkey_enabled(action):
                self._hotkeys.register(self._config.hotkey(action), emit)
        try:
            self._hotkeys.start()
        except PermissionError:
            self._notify(t("notify.hotkeys_need_input_monitoring"))
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
        try:
            screenshot = self._grab()
        finally:
            self._unshelve_settings_dialog()
        if screenshot is not None:
            self._deliver_capture(screenshot, self._app.primaryScreen().virtualGeometry())

    @Slot()
    def _capture_smart(self) -> None:
        self._shelve_settings_dialog()
        # Snapshot the window list *before* showing the overlay so our own
        # window isn't a target. An empty/failed list is fine — the overlay
        # then only offers full-screen and region modes.
        try:
            windows = self._capturer.list_windows()
        except Exception:
            windows = []
        # Resolve which on-screen windows are blocklisted up front, so the click
        # and hover-preview paths can refuse / skip them without re-querying.
        self._blocked_windows = {w.window_id: w for w in self._active_blocklist().blocked(windows)}
        screenshot = self._grab()
        if screenshot is None:
            self._unshelve_settings_dialog()
            return
        geometry = self._app.primaryScreen().virtualGeometry()
        overlay = SmartOverlay(
            screenshot,
            geometry,
            windows,
            window_preview=self._window_preview_image,
            hover_switch_delay_ms=self._config.hover_switch_delay_ms(),
        )
        # Region captures carry the full screenshot along so the editor can
        # keep the crop adjustable (arrow-key nudging) until annotation starts.
        overlay.region_selected.connect(
            lambda image, rect: self._deliver_capture(
                image, rect, region=RegionContext(screenshot, geometry)
            )
        )
        overlay.window_selected.connect(self._capture_window_image)
        overlay.fullscreen_selected.connect(lambda: self._deliver_capture(screenshot, geometry))
        self._track(overlay)
        # After _track: _forget must drop the overlay from _windows first, so
        # the unshelve check doesn't still count the dying overlay as alive.
        overlay.destroyed.connect(self._unshelve_settings_dialog)
        overlay.show()
        overlay.raise_()
        overlay.activateWindow()
        overlay.setFocus()

    def _window_preview_image(self, window_id: int) -> QImage | None:
        """One window's un-occluded pixels for the overlay's hover preview.

        Called from the overlay's preview thread (capture_window only talks to
        the window server, which is thread-safe; QImage is GUI-thread-free).
        Returns None on failure — the overlay then keeps the frozen screenshot.
        """
        if window_id in self._blocked_windows:
            return None  # never preview a blocklisted window's pixels
        try:
            return result_to_qimage(self._capturer.capture_window(window_id))
        except Exception:
            return None

    def _capture_window_image(self, window_id: int, origin: QRect) -> None:
        blocked = self._blocked_windows.get(window_id)
        if blocked is not None:
            self._notify(t("notify.capture_blocked").format(app=blocked.owner))
            return
        try:
            result = self._capturer.capture_window(window_id)
        except Exception as exc:
            self._notify(t("notify.capture_failed").format(error=exc))
            return
        self._deliver_capture(result_to_qimage(result), origin)

    def _grab(self) -> QImage | None:
        try:
            result = self._capturer.capture_fullscreen()
        except Exception as exc:
            self._notify(t("notify.capture_failed").format(error=exc))
            return None
        return result_to_qimage(self._redact_blocked(result))

    def _active_blocklist(self) -> bl.Blocklist:
        """The blocklist, reloaded per capture so Settings edits take effect.

        A corrupt file is treated as empty here (the GUI is interactive — the
        Settings editor surfaces the problem); the headless surface fails closed
        instead, where there is no one watching."""
        try:
            return bl.load()
        except bl.BlocklistError:
            return bl.Blocklist()

    def _redact_blocked(self, result):
        """Paint solid blocks over any blocklisted window in a full-screen grab,
        so a blocked app never reaches the editor, clipboard, or a saved file —
        the same protection the CLI/MCP get, on the human path."""
        blocklist = self._active_blocklist()
        if not blocklist:
            return result
        try:
            windows = self._capturer.list_windows()
        except Exception:
            return result  # can't enumerate → can't redact (macOS always can)
        blocked = blocklist.blocked(windows)
        if not blocked:
            return result
        redacted, _ = redact.redact_bounds(
            result, (result.origin_x, result.origin_y), [w.bounds for w in blocked]
        )
        return redacted

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
        pinned = PinnedWindow(image, origin)
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
        """Reveal the configured save directory in Finder.

        The folder is created on demand the same way the saver does it, so the
        menu item works even before the first capture has written anything —
        otherwise ``open`` would fail on a path that doesn't exist yet.
        """
        directory = Path(self._config.save_dir()).expanduser()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            subprocess.run(["open", str(directory)], check=True)
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
        print("No system tray / menu bar available on this system.", file=sys.stderr)
        return 1

    instance = ShotquillApp(app)
    exit_code = app.exec()
    instance.shutdown()
    return exit_code
