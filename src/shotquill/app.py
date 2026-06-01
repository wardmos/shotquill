# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Application entry point: a menu-bar-resident Qt app.

Phase 0 wires only the tray icon and a Quit action. Capture actions are filled
in during Phase 1 (full screen) and Phase 2 (region).
"""

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from shotquill.config import Config, human_readable_hotkey


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


def run() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Shotquill")
    app.setQuitOnLastWindowClosed(False)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("No system tray / menu bar available on this system.", file=sys.stderr)
        return 1

    config = Config()
    tray = QSystemTrayIcon(_build_icon())
    tray.setToolTip("Shotquill")

    menu = QMenu()
    region_key = human_readable_hotkey(config.hotkey("region_capture"))
    fullscreen_key = human_readable_hotkey(config.hotkey("fullscreen_capture"))

    region = QAction(f"区域截图\t{region_key}", menu)
    region.setEnabled(False)  # wired in Phase 2
    fullscreen = QAction(f"全屏截图\t{fullscreen_key}", menu)
    fullscreen.setEnabled(False)  # wired in Phase 1
    quit_action = QAction("退出 Shotquill", menu)
    quit_action.triggered.connect(app.quit)

    menu.addAction(region)
    menu.addAction(fullscreen)
    menu.addSeparator()
    menu.addAction(quit_action)

    tray.setContextMenu(menu)
    tray.show()
    return app.exec()
