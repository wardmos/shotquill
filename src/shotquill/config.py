# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""User configuration: hotkeys, save directory, image format.

Backed by QSettings (stored in the app's plist on macOS). Hotkey combos use
pynput's syntax (e.g. ``<alt>+a``) so they can be handed straight to the hotkey
manager. Qt is imported lazily so this module's pure helpers stay testable
without a running QApplication.
"""

from __future__ import annotations

from shotquill.i18n import DEFAULT_LANGUAGE

DEFAULT_HOTKEYS: dict[str, str] = {
    "region_capture": "<alt>+a",
    "fullscreen_capture": "<alt>+s",
    "window_capture": "<alt>+w",
}

# Each capture hotkey can be enabled/disabled independently; all on by default.
DEFAULT_HOTKEY_ENABLED = True

DEFAULT_IMAGE_FORMAT = "png"
DEFAULT_SAVE_DIR = "~/Pictures/ShotQuill"

# Capture feedback: a brief screen flash is on by default; the shutter sound is
# off by default (opt-in, to stay quiet and unobtrusive).
DEFAULT_FLASH = True
DEFAULT_SOUND = False
# Launch at login is off by default; enabling it installs a LaunchAgent.
DEFAULT_AUTOSTART = False
# Auto-output: when on, a capture is saved and/or copied immediately and the
# annotation editor is skipped (hands-free). Both default on; turn both off to
# get the manual annotate-then-save/copy flow back.
DEFAULT_AUTO_SAVE = True
DEFAULT_AUTO_COPY = True


def _to_bool(value: object, default: bool) -> bool:
    """Coerce a QSettings value (which may come back as a string) into a bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if value is None:
        return default
    return bool(value)


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

        self._settings = QSettings("wardmos", "ShotQuill")

    def hotkey(self, action: str) -> str:
        return str(self._settings.value(f"hotkeys/{action}", DEFAULT_HOTKEYS[action]))

    def set_hotkey(self, action: str, combo: str) -> None:
        self._settings.setValue(f"hotkeys/{action}", combo)

    def hotkey_enabled(self, action: str) -> bool:
        return _to_bool(self._settings.value(f"hotkeys/{action}_enabled"), DEFAULT_HOTKEY_ENABLED)

    def set_hotkey_enabled(self, action: str, enabled: bool) -> None:
        self._settings.setValue(f"hotkeys/{action}_enabled", bool(enabled))

    def image_format(self) -> str:
        return str(self._settings.value("output/format", DEFAULT_IMAGE_FORMAT))

    def set_image_format(self, image_format: str) -> None:
        self._settings.setValue("output/format", image_format)

    def save_dir(self) -> str:
        return str(self._settings.value("output/save_dir", DEFAULT_SAVE_DIR))

    def set_save_dir(self, directory: str) -> None:
        self._settings.setValue("output/save_dir", directory)

    def language(self) -> str:
        return str(self._settings.value("ui/language", DEFAULT_LANGUAGE))

    def set_language(self, language: str) -> None:
        self._settings.setValue("ui/language", language)

    def flash_on_capture(self) -> bool:
        return _to_bool(self._settings.value("feedback/flash"), DEFAULT_FLASH)

    def set_flash_on_capture(self, enabled: bool) -> None:
        self._settings.setValue("feedback/flash", bool(enabled))

    def sound_on_capture(self) -> bool:
        return _to_bool(self._settings.value("feedback/sound"), DEFAULT_SOUND)

    def set_sound_on_capture(self, enabled: bool) -> None:
        self._settings.setValue("feedback/sound", bool(enabled))

    def autostart(self) -> bool:
        return _to_bool(self._settings.value("startup/autostart"), DEFAULT_AUTOSTART)

    def set_autostart(self, enabled: bool) -> None:
        self._settings.setValue("startup/autostart", bool(enabled))

    def auto_save_after_capture(self) -> bool:
        return _to_bool(self._settings.value("output/auto_save"), DEFAULT_AUTO_SAVE)

    def set_auto_save_after_capture(self, enabled: bool) -> None:
        self._settings.setValue("output/auto_save", bool(enabled))

    def auto_copy_after_capture(self) -> bool:
        return _to_bool(self._settings.value("output/auto_copy"), DEFAULT_AUTO_COPY)

    def set_auto_copy_after_capture(self, enabled: bool) -> None:
        self._settings.setValue("output/auto_copy", bool(enabled))
