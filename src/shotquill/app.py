# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Application entry point: a menu-bar-resident Qt app.

Full-screen (``⌥S``) and region (``⌥A``) capture both open the annotation
editor — region capture first lets the user drag a selection on a frozen,
dimmed screenshot.
"""

import subprocess
import sys

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from shotquill.capture.macos import MacScreenCapturer
from shotquill.config import Config, human_readable_hotkey
from shotquill.hotkeys.macos import MacHotkeyManager
from shotquill.imaging import result_to_qimage
from shotquill.ui.editor import EditorWindow
from shotquill.ui.overlay import RegionOverlay

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


class ShotquillApp:
    """Owns the tray icon, hotkeys, capturer, overlays, and editor windows."""

    def __init__(self, app: QApplication) -> None:
        self._app = app
        self._config = Config()
        self._capturer = MacScreenCapturer()
        self._hotkeys = MacHotkeyManager()
        self._windows: list[object] = []  # keep overlays/editors alive

        self._bridge = _HotkeyBridge()
        # Queued (cross-thread) connections: slots run on the main thread.
        self._bridge.region_requested.connect(self._capture_region)
        self._bridge.fullscreen_requested.connect(self._capture_fullscreen)

        self._tray = self._build_tray()
        self._register_hotkeys()

    def _build_tray(self) -> QSystemTrayIcon:
        tray = QSystemTrayIcon(_build_icon(), self._app)
        tray.setToolTip("Shotquill")

        menu = QMenu()
        region_key = human_readable_hotkey(self._config.hotkey("region_capture"))
        fullscreen_key = human_readable_hotkey(self._config.hotkey("fullscreen_capture"))

        region = QAction(f"区域截图\t{region_key}", menu)
        region.triggered.connect(self._capture_region)

        fullscreen = QAction(f"全屏截图\t{fullscreen_key}", menu)
        fullscreen.triggered.connect(self._capture_fullscreen)

        permissions = QAction("打开屏幕录制权限设置…", menu)
        permissions.triggered.connect(self._open_privacy_settings)

        quit_action = QAction("退出 Shotquill", menu)
        quit_action.triggered.connect(self._app.quit)

        menu.addAction(region)
        menu.addAction(fullscreen)
        menu.addSeparator()
        menu.addAction(permissions)
        menu.addAction(quit_action)

        tray.setContextMenu(menu)
        tray.show()
        return tray

    def _register_hotkeys(self) -> None:
        self._hotkeys.register(
            self._config.hotkey("region_capture"),
            self._bridge.region_requested.emit,
        )
        self._hotkeys.register(
            self._config.hotkey("fullscreen_capture"),
            self._bridge.fullscreen_requested.emit,
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

    def _grab(self) -> QImage | None:
        try:
            result = self._capturer.capture_fullscreen()
        except Exception as exc:
            self._tray.showMessage(
                "Shotquill",
                f"截图失败：{exc}",
                QSystemTrayIcon.MessageIcon.Critical,
            )
            return None
        return result_to_qimage(result)

    def _open_editor(self, image: QImage) -> None:
        editor = EditorWindow(image, self._config)
        self._track(editor)
        editor.show()
        editor.raise_()
        editor.activateWindow()

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
    app.setApplicationName("Shotquill")
    app.setQuitOnLastWindowClosed(False)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("No system tray / menu bar available on this system.", file=sys.stderr)
        return 1

    instance = ShotquillApp(app)
    exit_code = app.exec()
    instance.shutdown()
    return exit_code
