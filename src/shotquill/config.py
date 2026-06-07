# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""User configuration: hotkeys, save directory, image format.

Backed by QSettings (stored in the app's plist on macOS). Two hotkey syntaxes
coexist under the ``hotkeys/`` group: *capture* hotkeys use pynput's syntax
(e.g. ``<alt>+a``) so they can be handed straight to the global hotkey manager,
while *editor finish keys* use Qt's QKeySequence portable syntax (e.g. ``Space``,
``Ctrl+Return``) since they are matched inside Qt windows — always read them
through their dedicated accessors (``hotkey`` vs ``editor_hotkey``). Qt is
imported lazily so this module's pure helpers stay testable without a running
QApplication.
"""

from __future__ import annotations

from shotquill.i18n import DEFAULT_LANGUAGE

DEFAULT_HOTKEYS: dict[str, str] = {
    "smart_capture": "<alt>+a",  # pointer picks window / full-screen / region
    "fullscreen_capture": "<alt>+s",
}

# Each capture hotkey can be enabled/disabled independently; all on by default.
DEFAULT_HOTKEY_ENABLED = True

# In-editor finish keys (Qt QKeySequence portable syntax, not pynput): Space
# copies the annotated shot to the clipboard, Enter saves it to the folder.
# Both close the editor. Configurable and individually toggleable in Settings.
DEFAULT_EDITOR_HOTKEYS: dict[str, str] = {
    "editor_copy": "Space",
    "editor_save": "Return",
}

DEFAULT_IMAGE_FORMAT = "png"
DEFAULT_SAVE_DIR = "~/Pictures/ShotQuill"

# Screenshots leave the mouse pointer out by default; including it is opt-in.
DEFAULT_INCLUDE_CURSOR = False

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

# How long the pointer must rest on a new target before the capture overlay
# switches its highlighted window, in milliseconds. 0 switches the moment the
# pointer crosses a window edge; HOVER_SWITCH_NEVER turns automatic switching
# off entirely — a window is then only selected by clicking it. That is the
# default: timed switching either reads as a hang (long delays) or strobes
# the preview through every window swept over (short ones).
HOVER_SWITCH_NEVER = -1
DEFAULT_HOVER_SWITCH_DELAY_MS = HOVER_SWITCH_NEVER

# Region capture: releasing the drag pins the selection for pixel-accurate
# keyboard adjustment (arrow keys nudge, Enter/click captures) instead of
# capturing immediately. Turning this off restores capture-on-release.
DEFAULT_REGION_ADJUST = True

# The annotation editor opens as a frameless "spotlight": no macOS title bar
# (and thus no traffic-light buttons), with the desktop around it kept dimmed
# like during capture. Turning this off restores a regular titled window.
DEFAULT_EDITOR_BACKDROP = True

# How the editor toolbar labels its buttons: an icon next to the text (the
# default), just the icon (compact; the text moves into the tooltip), or just
# the text (the classic pre-icon look).
TOOLBAR_STYLES = ("both", "icon", "text")
DEFAULT_TOOLBAR_STYLE = "both"


def _to_bool(value: object, default: bool) -> bool:
    """Coerce a QSettings value (which may come back as a string) into a bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if value is None:
        return default
    return bool(value)


def _to_int(value: object, default: int) -> int:
    """Coerce a QSettings value (which may come back as a string) into an int."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


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

    def editor_hotkey(self, action: str) -> str:
        return str(self._settings.value(f"hotkeys/{action}", DEFAULT_EDITOR_HOTKEYS[action]))

    def set_editor_hotkey(self, action: str, sequence: str) -> None:
        self._settings.setValue(f"hotkeys/{action}", sequence)

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

    def include_cursor(self) -> bool:
        return _to_bool(self._settings.value("capture/include_cursor"), DEFAULT_INCLUDE_CURSOR)

    def set_include_cursor(self, enabled: bool) -> None:
        self._settings.setValue("capture/include_cursor", bool(enabled))

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

    def hover_switch_delay_ms(self) -> int:
        """Overlay highlight-switch delay; any negative value reads as NEVER."""
        value = _to_int(
            self._settings.value("capture/hover_switch_delay_ms"), DEFAULT_HOVER_SWITCH_DELAY_MS
        )
        return HOVER_SWITCH_NEVER if value < 0 else value

    def set_hover_switch_delay_ms(self, delay_ms: int) -> None:
        self._settings.setValue("capture/hover_switch_delay_ms", int(delay_ms))

    def region_adjust(self) -> bool:
        return _to_bool(self._settings.value("capture/region_adjust"), DEFAULT_REGION_ADJUST)

    def set_region_adjust(self, enabled: bool) -> None:
        self._settings.setValue("capture/region_adjust", bool(enabled))

    def editor_backdrop(self) -> bool:
        return _to_bool(self._settings.value("editor/backdrop"), DEFAULT_EDITOR_BACKDROP)

    def set_editor_backdrop(self, enabled: bool) -> None:
        self._settings.setValue("editor/backdrop", bool(enabled))

    def toolbar_style(self) -> str:
        """One of TOOLBAR_STYLES; unknown stored values fall back to the default."""
        value = str(self._settings.value("editor/toolbar_style", DEFAULT_TOOLBAR_STYLE))
        return value if value in TOOLBAR_STYLES else DEFAULT_TOOLBAR_STYLE

    def set_toolbar_style(self, style: str) -> None:
        self._settings.setValue("editor/toolbar_style", style)
