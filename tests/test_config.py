# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
from shotquill.config import (
    DEFAULT_HOTKEYS,
    DEFAULT_IMAGE_FORMAT,
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
