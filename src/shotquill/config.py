# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""User configuration: hotkeys, save directory, image format.

Backed by QSettings (stored in the app's plist on macOS). Hotkey combos use
pynput's syntax (e.g. ``<alt>+a``) so they can be handed straight to the hotkey
manager. Qt is imported lazily so this module's pure helpers stay testable
without a running QApplication.
"""

from __future__ import annotations

DEFAULT_HOTKEYS: dict[str, str] = {
    "region_capture": "<alt>+a",
    "fullscreen_capture": "<alt>+s",
}

DEFAULT_IMAGE_FORMAT = "png"
DEFAULT_SAVE_DIR = "~/Pictures/Shotquill"

_MODIFIER_SYMBOLS = {
    "<alt>": "⌥",
    "<cmd>": "⌘",
    "<ctrl>": "⌃",
    "<shift>": "⇧",
}


def human_readable_hotkey(combo: str) -> str:
    """Convert a pynput combo like ``<alt>+a`` into a display string like ``⌥A``."""
    parts = [p.strip() for p in combo.split("+") if p.strip()]
    return "".join(_MODIFIER_SYMBOLS.get(p.lower(), p.upper()) for p in parts)


class Config:
    """Thin wrapper over QSettings for persisting user preferences."""

    def __init__(self) -> None:
        from PySide6.QtCore import QSettings

        self._settings = QSettings("wardmos", "Shotquill")

    def hotkey(self, action: str) -> str:
        return str(self._settings.value(f"hotkeys/{action}", DEFAULT_HOTKEYS[action]))

    def set_hotkey(self, action: str, combo: str) -> None:
        self._settings.setValue(f"hotkeys/{action}", combo)

    def image_format(self) -> str:
        return str(self._settings.value("output/format", DEFAULT_IMAGE_FORMAT))

    def set_image_format(self, image_format: str) -> None:
        self._settings.setValue("output/format", image_format)

    def save_dir(self) -> str:
        return str(self._settings.value("output/save_dir", DEFAULT_SAVE_DIR))

    def set_save_dir(self, directory: str) -> None:
        self._settings.setValue("output/save_dir", directory)
