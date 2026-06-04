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

from PySide6.QtCore import QObject, QRect, Qt, Signal, Slot
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from shotquill import __version__
from shotquill.autostart.macos import MacAutostartManager
from shotquill.capture.macos import MacScreenCapturer
from shotquill.config import Config, human_readable_hotkey
from shotquill.hotkeys.macos import MacHotkeyManager
from shotquill.i18n import set_language, t
from shotquill.imaging import result_to_qimage
from shotquill.ui.editor import EditorWindow
from shotquill.ui.feedback import CaptureFeedback
from shotquill.ui.pinned import PinnedWindow
from shotquill.ui.settings import SettingsDialog
from shotquill.ui.smart_overlay import SmartOverlay

_PRIVACY_SCREEN_CAPTURE = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
)
# Global hotkeys require Input Monitoring, a *different* pane from screen capture.
_PRIVACY_INPUT_MONITORING = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
)


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
        settings = QAction(t("menu.settings"), menu)
        settings.triggered.connect(self._open_settings)
        permissions = QAction(t("menu.permissions"), menu)
        permissions.triggered.connect(self._open_privacy_settings)
        input_monitoring = QAction(t("menu.input_monitoring"), menu)
        input_monitoring.triggered.connect(self._open_input_monitoring_settings)
        about = QAction(t("menu.about"), menu)
        about.triggered.connect(self._show_about)
        quit_action = QAction(t("menu.quit"), menu)
        quit_action.triggered.connect(self._app.quit)

        menu.addAction(smart)
        menu.addAction(fullscreen)
        menu.addSeparator()
        menu.addAction(settings)
        menu.addAction(permissions)
        menu.addAction(input_monitoring)
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
            self._open_input_monitoring_settings()

    @Slot()
    def _capture_fullscreen(self) -> None:
        screenshot = self._grab()
        if screenshot is not None:
            self._deliver_capture(screenshot, self._app.primaryScreen().virtualGeometry())

    @Slot()
    def _capture_smart(self) -> None:
        # Snapshot the window list *before* showing the overlay so our own
        # window isn't a target. An empty/failed list is fine — the overlay
        # then only offers full-screen and region modes.
        try:
            windows = self._capturer.list_windows()
        except Exception:
            windows = []
        screenshot = self._grab()
        if screenshot is None:
            return
        geometry = self._app.primaryScreen().virtualGeometry()
        overlay = SmartOverlay(screenshot, geometry, windows)
        overlay.region_selected.connect(self._deliver_capture)
        overlay.window_selected.connect(self._capture_window_image)
        overlay.fullscreen_selected.connect(lambda: self._deliver_capture(screenshot, geometry))
        self._track(overlay)
        overlay.show()
        overlay.raise_()
        overlay.activateWindow()
        overlay.setFocus()

    def _capture_window_image(self, window_id: int, origin: QRect) -> None:
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
        return result_to_qimage(result)

    def _notify(self, message: str) -> None:
        self._tray.showMessage("ShotQuill", message, QSystemTrayIcon.MessageIcon.Critical)

    def _deliver_capture(self, image: QImage, origin: QRect | None = None) -> None:
        # Single exit for every capture mode. Flash/sound feedback fires either
        # way; then auto-output (if enabled) saves/copies the raw shot hands-free
        # and skips the editor. With both auto toggles off, the editor opens —
        # placed over ``origin`` (the shot's on-screen rect) when known.
        self._signal_capture()
        if self._auto_output(image):
            return
        self._open_editor(image, origin)

    def _auto_output(self, image: QImage) -> bool:
        """Save and/or copy the raw shot per config; return True if it handled it."""
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
                self._notify(t("notify.capture_failed").format(error=exc))
        return True

    def _open_editor(self, image: QImage, origin: QRect | None = None) -> None:
        editor = EditorWindow(image, self._config, origin)
        editor.pin_requested.connect(self._pin_image)
        self._track(editor)
        editor.show()
        editor.raise_()
        editor.activateWindow()

    def _pin_image(self, image: QImage) -> None:
        pinned = PinnedWindow(image)
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

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self._config)
        if dialog.exec():
            set_language(self._config.language())
            self._capturer.include_cursor = self._config.include_cursor()
            self._apply_hotkeys()
            self._sync_autostart()
            self._rebuild_menu()

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

    def _open_privacy_settings(self) -> None:
        subprocess.run(["open", _PRIVACY_SCREEN_CAPTURE], check=False)

    def _open_input_monitoring_settings(self) -> None:
        subprocess.run(["open", _PRIVACY_INPUT_MONITORING], check=False)

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
