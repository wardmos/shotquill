# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Application entry point: a menu-bar-resident Qt app.

Full-screen (``⌥S``), region (``⌥A``), and window (``⌥W``) capture all open the
annotation editor. Hotkeys, save directory, image format, and UI language are
editable in Settings.
"""

from __future__ import annotations

import subprocess
import sys

from PySide6.QtCore import QObject, Qt, Signal
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
from shotquill.ui.overlay import RegionOverlay
from shotquill.ui.pinned import PinnedWindow
from shotquill.ui.settings import SettingsDialog
from shotquill.ui.window_picker import WindowPicker

_PRIVACY_SCREEN_CAPTURE = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
)


def _build_icon() -> QIcon:
    """Draw a simple placeholder menu-bar icon (no external asset needed yet)."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#2d7ff9"))
    painter.drawRoundedRect(6, 6, 52, 52, 14, 14)
    font = QFont()
    font.setBold(True)
    font.setPointSize(28)
    painter.setFont(font)
    painter.setPen(QColor("white"))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "S")
    painter.end()
    return QIcon(pixmap)


class _HotkeyBridge(QObject):
    """Marshals hotkey events from the pynput listener thread onto the Qt main thread."""

    region_requested = Signal()
    fullscreen_requested = Signal()
    window_requested = Signal()


class ShotquillApp:
    """Owns the tray icon, hotkeys, capturer, overlays, and editor windows."""

    def __init__(self, app: QApplication) -> None:
        self._app = app
        self._config = Config()
        set_language(self._config.language())

        self._capturer = MacScreenCapturer()
        self._hotkeys = MacHotkeyManager()
        self._feedback = CaptureFeedback()
        self._autostart = MacAutostartManager()
        self._sync_autostart()
        self._windows: list[object] = []  # keep overlays/editors alive

        self._bridge = _HotkeyBridge()
        # Queued (cross-thread) connections: slots run on the main thread.
        self._bridge.region_requested.connect(self._capture_region)
        self._bridge.fullscreen_requested.connect(self._capture_fullscreen)
        self._bridge.window_requested.connect(self._capture_window)

        self._tray = QSystemTrayIcon(_build_icon(), self._app)
        self._tray.setToolTip("ShotQuill")
        self._rebuild_menu()
        self._tray.show()
        self._apply_hotkeys()

    def _rebuild_menu(self) -> None:
        """(Re)build the tray menu — used at startup and after a language change."""
        menu = QMenu()
        region_key = human_readable_hotkey(self._config.hotkey("region_capture"))
        fullscreen_key = human_readable_hotkey(self._config.hotkey("fullscreen_capture"))
        window_key = human_readable_hotkey(self._config.hotkey("window_capture"))

        region = QAction(f"{t('menu.region')}\t{region_key}", menu)
        region.triggered.connect(self._capture_region)
        fullscreen = QAction(f"{t('menu.fullscreen')}\t{fullscreen_key}", menu)
        fullscreen.triggered.connect(self._capture_fullscreen)
        window = QAction(f"{t('menu.window')}\t{window_key}", menu)
        window.triggered.connect(self._capture_window)
        settings = QAction(t("menu.settings"), menu)
        settings.triggered.connect(self._open_settings)
        permissions = QAction(t("menu.permissions"), menu)
        permissions.triggered.connect(self._open_privacy_settings)
        about = QAction(t("menu.about"), menu)
        about.triggered.connect(self._show_about)
        quit_action = QAction(t("menu.quit"), menu)
        quit_action.triggered.connect(self._app.quit)

        menu.addAction(region)
        menu.addAction(fullscreen)
        menu.addAction(window)
        menu.addSeparator()
        menu.addAction(settings)
        menu.addAction(permissions)
        menu.addAction(about)
        menu.addSeparator()
        menu.addAction(quit_action)

        self._tray.setContextMenu(menu)
        self._menu = menu  # keep a reference

    def _apply_hotkeys(self) -> None:
        self._hotkeys.stop()
        self._hotkeys.clear()
        self._hotkeys.register(
            self._config.hotkey("region_capture"),
            self._bridge.region_requested.emit,
        )
        self._hotkeys.register(
            self._config.hotkey("fullscreen_capture"),
            self._bridge.fullscreen_requested.emit,
        )
        self._hotkeys.register(
            self._config.hotkey("window_capture"),
            self._bridge.window_requested.emit,
        )
        self._hotkeys.start()

    def _capture_fullscreen(self) -> None:
        screenshot = self._grab()
        if screenshot is not None:
            self._open_editor(screenshot)

    def _capture_region(self) -> None:
        screenshot = self._grab()
        if screenshot is None:
            return
        overlay = RegionOverlay(screenshot, self._app.primaryScreen().virtualGeometry())
        overlay.region_selected.connect(self._open_editor)
        self._track(overlay)
        overlay.show()
        overlay.raise_()
        overlay.activateWindow()
        overlay.setFocus()

    def _capture_window(self) -> None:
        # Snapshot the window list *before* showing the picker so our own
        # overlay isn't a target, then let the user point at a window.
        try:
            windows = self._capturer.list_windows()
        except Exception as exc:
            self._notify(t("notify.capture_failed").format(error=exc))
            return
        if not windows:
            self._notify(t("notify.no_windows"))
            return
        screenshot = self._grab()
        if screenshot is None:
            return
        picker = WindowPicker(screenshot, self._app.primaryScreen().virtualGeometry(), windows)
        picker.window_selected.connect(self._capture_window_image)
        self._track(picker)
        picker.show()
        picker.raise_()
        picker.activateWindow()
        picker.setFocus()

    def _capture_window_image(self, window_id: int) -> None:
        try:
            result = self._capturer.capture_window(window_id)
        except Exception as exc:
            self._notify(t("notify.capture_failed").format(error=exc))
            return
        self._open_editor(result_to_qimage(result))

    def _grab(self) -> QImage | None:
        try:
            result = self._capturer.capture_fullscreen()
        except Exception as exc:
            self._notify(t("notify.capture_failed").format(error=exc))
            return None
        return result_to_qimage(result)

    def _notify(self, message: str) -> None:
        self._tray.showMessage("ShotQuill", message, QSystemTrayIcon.MessageIcon.Critical)

    def _open_editor(self, image: QImage) -> None:
        self._signal_capture()
        editor = EditorWindow(image, self._config)
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
