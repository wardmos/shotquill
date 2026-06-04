# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
from shotquill.config import (
    DEFAULT_AUTO_COPY,
    DEFAULT_AUTO_SAVE,
    DEFAULT_AUTOSTART,
    DEFAULT_FLASH,
    DEFAULT_HOTKEYS,
    DEFAULT_IMAGE_FORMAT,
    DEFAULT_INCLUDE_CURSOR,
    DEFAULT_SOUND,
    _to_bool,
    human_readable_hotkey,
)


def test_default_hotkeys():
    assert DEFAULT_HOTKEYS["smart_capture"] == "<alt>+a"
    assert DEFAULT_HOTKEYS["fullscreen_capture"] == "<alt>+s"
    assert "window_capture" not in DEFAULT_HOTKEYS


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


def test_config_persists_across_instances(config):
    from shotquill.config import Config

    config.set_language("zh")
    config.set_image_format("jpg")
    # A fresh wrapper reads the same backing store.
    reopened = Config()
    assert reopened.language() == "zh"
    assert reopened.image_format() == "jpg"
