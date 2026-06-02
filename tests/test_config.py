# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
from shotquill.config import (
    DEFAULT_AUTOSTART,
    DEFAULT_FLASH,
    DEFAULT_HOTKEYS,
    DEFAULT_IMAGE_FORMAT,
    DEFAULT_SOUND,
    _to_bool,
    human_readable_hotkey,
)


def test_default_hotkeys():
    assert DEFAULT_HOTKEYS["region_capture"] == "<alt>+a"
    assert DEFAULT_HOTKEYS["fullscreen_capture"] == "<alt>+s"


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
