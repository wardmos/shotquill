# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
from shotquill.config import (
    DEFAULT_AUTO_COPY,
    DEFAULT_AUTO_SAVE,
    DEFAULT_AUTOSTART,
    DEFAULT_EDITOR_HOTKEYS,
    DEFAULT_FLASH,
    DEFAULT_HOTKEYS,
    DEFAULT_HOVER_SWITCH_DELAY_MS,
    DEFAULT_IMAGE_FORMAT,
    DEFAULT_INCLUDE_CURSOR,
    DEFAULT_SOUND,
    HOVER_SWITCH_NEVER,
    _to_bool,
    _to_int,
    human_readable_hotkey,
)


def test_default_hotkeys():
    assert DEFAULT_HOTKEYS["smart_capture"] == "<alt>+a"
    assert DEFAULT_HOTKEYS["fullscreen_capture"] == "<alt>+s"
    assert "window_capture" not in DEFAULT_HOTKEYS


def test_default_editor_hotkeys():
    # Space copies to the clipboard, Enter saves to the folder.
    assert DEFAULT_EDITOR_HOTKEYS["editor_copy"] == "Space"
    assert DEFAULT_EDITOR_HOTKEYS["editor_save"] == "Return"


def test_default_image_format():
    assert DEFAULT_IMAGE_FORMAT == "png"


def test_human_readable_hotkey():
    assert human_readable_hotkey("<alt>+a") == "⌥A"
    assert human_readable_hotkey("<alt>+s") == "⌥S"
    assert human_readable_hotkey("<ctrl>+<cmd>+1") == "⌃⌘1"


def test_feedback_defaults():
    # Flash is on by default; the shutter sound is opt-in (off by default).
    assert DEFAULT_FLASH is True
    assert DEFAULT_SOUND is False
    assert DEFAULT_AUTOSTART is False


def test_cursor_excluded_by_default():
    # Screenshots leave the mouse pointer out unless the user opts in.
    assert DEFAULT_INCLUDE_CURSOR is False


def test_auto_output_defaults_on():
    # Hands-free by default: a capture is saved AND copied without the editor.
    assert DEFAULT_AUTO_SAVE is True
    assert DEFAULT_AUTO_COPY is True


def test_hover_switch_defaults():
    # The overlay waits 3 s before switching its highlighted window; NEVER is
    # the "only switch when a window is clicked" sentinel.
    assert DEFAULT_HOVER_SWITCH_DELAY_MS == 3000
    assert HOVER_SWITCH_NEVER < 0


def test_to_int_parses_qsettings_strings():
    # QSettings can round-trip ints as strings depending on the backend.
    assert _to_int("3000", 0) == 3000
    assert _to_int("-1", 0) == -1
    assert _to_int(None, 7) == 7
    assert _to_int("garbage", 7) == 7


def test_to_bool_falls_back_to_default_when_unset():
    assert _to_bool(None, True) is True
    assert _to_bool(None, False) is False


def test_to_bool_parses_qsettings_strings():
    # QSettings can round-trip bools as strings depending on the backend.
    assert _to_bool("true", False) is True
    assert _to_bool("false", True) is False
    assert _to_bool("1", False) is True
    assert _to_bool("0", True) is False
    assert _to_bool(True, False) is True


def test_human_readable_hotkey_ignores_blank_segments():
    assert human_readable_hotkey("") == ""
    assert human_readable_hotkey("<alt>++a") == "⌥A"


# --- Config (backed by an isolated temp QSettings via the `config` fixture) ---


def test_config_returns_defaults_when_unset(config):
    assert config.hotkey("smart_capture") == DEFAULT_HOTKEYS["smart_capture"]
    assert config.hotkey("fullscreen_capture") == DEFAULT_HOTKEYS["fullscreen_capture"]
    assert config.image_format() == DEFAULT_IMAGE_FORMAT
    assert config.save_dir().endswith("ShotQuill")
    assert config.language() == "en"
    assert config.flash_on_capture() is DEFAULT_FLASH
    assert config.sound_on_capture() is DEFAULT_SOUND
    assert config.autostart() is DEFAULT_AUTOSTART
    assert config.include_cursor() is DEFAULT_INCLUDE_CURSOR


def test_config_hotkey_round_trip(config):
    config.set_hotkey("smart_capture", "<cmd>+<shift>+a")
    assert config.hotkey("smart_capture") == "<cmd>+<shift>+a"
    # The other action is untouched.
    assert config.hotkey("fullscreen_capture") == DEFAULT_HOTKEYS["fullscreen_capture"]


def test_config_editor_hotkey_defaults_and_round_trip(config):
    assert config.editor_hotkey("editor_copy") == DEFAULT_EDITOR_HOTKEYS["editor_copy"]
    assert config.editor_hotkey("editor_save") == DEFAULT_EDITOR_HOTKEYS["editor_save"]
    config.set_editor_hotkey("editor_copy", "Ctrl+Return")
    assert config.editor_hotkey("editor_copy") == "Ctrl+Return"
    # The other action is untouched.
    assert config.editor_hotkey("editor_save") == DEFAULT_EDITOR_HOTKEYS["editor_save"]


def test_editor_hotkeys_enabled_by_default(config):
    assert config.hotkey_enabled("editor_copy") is True
    assert config.hotkey_enabled("editor_save") is True


def test_config_scalar_round_trips(config):
    config.set_image_format("jpg")
    config.set_save_dir("/tmp/shots")
    config.set_language("zh")
    assert config.image_format() == "jpg"
    assert config.save_dir() == "/tmp/shots"
    assert config.language() == "zh"


def test_hotkeys_enabled_by_default(config):
    assert config.hotkey_enabled("smart_capture") is True
    assert config.hotkey_enabled("fullscreen_capture") is True


def test_hotkey_enabled_round_trip(config):
    config.set_hotkey_enabled("smart_capture", False)
    assert config.hotkey_enabled("smart_capture") is False
    # Other actions stay enabled.
    assert config.hotkey_enabled("fullscreen_capture") is True


def test_config_bool_round_trips(config):
    config.set_flash_on_capture(False)
    config.set_sound_on_capture(True)
    config.set_autostart(True)
    config.set_include_cursor(True)
    assert config.flash_on_capture() is False
    assert config.sound_on_capture() is True
    assert config.autostart() is True
    assert config.include_cursor() is True


def test_hover_switch_delay_round_trip_and_default(config):
    assert config.hover_switch_delay_ms() == DEFAULT_HOVER_SWITCH_DELAY_MS
    config.set_hover_switch_delay_ms(0)
    assert config.hover_switch_delay_ms() == 0
    config.set_hover_switch_delay_ms(HOVER_SWITCH_NEVER)
    assert config.hover_switch_delay_ms() == HOVER_SWITCH_NEVER
    # Any negative value (e.g. hand-edited prefs) normalizes to NEVER.
    config.set_hover_switch_delay_ms(-42)
    assert config.hover_switch_delay_ms() == HOVER_SWITCH_NEVER


def test_config_persists_across_instances(config):
    from shotquill.config import Config

    config.set_language("zh")
    config.set_image_format("jpg")
    # A fresh wrapper reads the same backing store.
    reopened = Config()
    assert reopened.language() == "zh"
    assert reopened.image_format() == "jpg"
