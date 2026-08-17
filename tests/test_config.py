# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
from shotquill.config import (
    DEFAULT_AUTO_COPY,
    DEFAULT_AUTO_SAVE,
    DEFAULT_AUTOSTART,
    DEFAULT_DEBUG_MODE,
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


def test_human_readable_hotkey_mac_style():
    # mac_style=True forces the Apple keycap glyphs regardless of the host
    # platform — matching macOS menu convention.
    assert human_readable_hotkey("<alt>+a", mac_style=True) == "⌥A"
    assert human_readable_hotkey("<alt>+s", mac_style=True) == "⌥S"
    assert human_readable_hotkey("<ctrl>+<cmd>+1", mac_style=True) == "⌃⌘1"


def test_human_readable_hotkey_text_style_for_linux():
    # mac_style=False renders the labels every Linux desktop actually uses —
    # `⌥A` in a GNOME tray menu reads as a hieroglyph; `Alt+A` reads as the
    # key the user already presses. ``<cmd>`` becomes ``Super+`` because the
    # Mac Cmd key maps to Super/Meta on Linux keyboards.
    assert human_readable_hotkey("<alt>+a", mac_style=False) == "Alt+A"
    assert human_readable_hotkey("<alt>+s", mac_style=False) == "Alt+S"
    assert human_readable_hotkey("<ctrl>+<shift>+f5", mac_style=False) == "Ctrl+Shift+F5"
    assert human_readable_hotkey("<cmd>+a", mac_style=False) == "Super+A"


def test_human_readable_hotkey_default_follows_platform(monkeypatch):
    # The default (mac_style=None) auto-detects so app code doesn't have to
    # thread the platform through every menu rebuild.
    import shotquill.config as config_module

    monkeypatch.setattr(config_module.sys, "platform", "darwin", raising=False)
    assert human_readable_hotkey("<alt>+a") == "⌥A"
    monkeypatch.setattr(config_module.sys, "platform", "linux", raising=False)
    assert human_readable_hotkey("<alt>+a") == "Alt+A"


def test_feedback_defaults():
    # Flash is on by default; the shutter sound is opt-in (off by default).
    assert DEFAULT_FLASH is True
    assert DEFAULT_SOUND is False
    assert DEFAULT_AUTOSTART is False


def test_cursor_excluded_by_default():
    # Screenshots leave the mouse pointer out unless the user opts in.
    assert DEFAULT_INCLUDE_CURSOR is False


def test_auto_output_defaults_off():
    # Manual by default: a capture opens the editor; neither auto-save nor
    # auto-copy fires until the user opts in.
    assert DEFAULT_AUTO_SAVE is False
    assert DEFAULT_AUTO_COPY is False


def test_debug_mode_defaults_off():
    assert DEFAULT_DEBUG_MODE is False


def test_hover_switch_defaults():
    # By default the overlay only switches its highlighted window when one is
    # clicked; NEVER is the sentinel for that mode (any negative value).
    assert DEFAULT_HOVER_SWITCH_DELAY_MS == HOVER_SWITCH_NEVER
    assert HOVER_SWITCH_NEVER < 0


def test_scrolling_height_limits_are_safe_and_configurable():
    from shotquill.config import (
        SCROLL_MAX_HEIGHT_CHOICES,
        SCROLL_MAX_HEIGHT_DEFAULT,
        SCROLL_MAX_HEIGHT_HARD_LIMIT,
        SCROLL_MAX_PIXELS,
    )

    assert SCROLL_MAX_HEIGHT_DEFAULT == 20_000
    assert SCROLL_MAX_HEIGHT_CHOICES == (20_000, 30_000, 40_000, 50_000)
    assert SCROLL_MAX_HEIGHT_HARD_LIMIT == 50_000
    assert SCROLL_MAX_PIXELS == 64_000_000


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


def test_to_bool_coerces_other_types_via_bool():
    # A value that is neither bool, str, nor None (e.g. an int a QSettings
    # backend hands back) falls through to bool() — not the default.
    assert _to_bool(0, True) is False
    assert _to_bool(2, False) is True


def test_human_readable_hotkey_ignores_blank_segments():
    # Pin mac_style so the assertion doesn't drift with the host platform
    # (default = sys.platform-dependent, ``Alt+A`` off-mac).
    assert human_readable_hotkey("", mac_style=True) == ""
    assert human_readable_hotkey("<alt>++a", mac_style=True) == "⌥A"
    assert human_readable_hotkey("", mac_style=False) == ""
    assert human_readable_hotkey("<alt>++a", mac_style=False) == "Alt+A"


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
    assert config.debug_mode() is DEFAULT_DEBUG_MODE


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
    config.set_debug_mode(True)
    assert config.flash_on_capture() is False
    assert config.sound_on_capture() is True
    assert config.autostart() is True
    assert config.include_cursor() is True
    assert config.debug_mode() is True


def test_region_adjust_defaults_on(config):
    from shotquill.config import DEFAULT_REGION_ADJUST

    # Releasing a region drag pins the selection for keyboard nudging.
    assert DEFAULT_REGION_ADJUST is True
    assert config.region_adjust() is True


def test_region_adjust_round_trip(config):
    config.set_region_adjust(False)
    assert config.region_adjust() is False
    config.set_region_adjust(True)
    assert config.region_adjust() is True


def test_hover_switch_delay_round_trip_and_default(config):
    assert config.hover_switch_delay_ms() == DEFAULT_HOVER_SWITCH_DELAY_MS
    config.set_hover_switch_delay_ms(0)
    assert config.hover_switch_delay_ms() == 0
    config.set_hover_switch_delay_ms(HOVER_SWITCH_NEVER)
    assert config.hover_switch_delay_ms() == HOVER_SWITCH_NEVER
    # Any negative value (e.g. hand-edited prefs) normalizes to NEVER.
    config.set_hover_switch_delay_ms(-42)
    assert config.hover_switch_delay_ms() == HOVER_SWITCH_NEVER


def test_scrolling_max_height_round_trip_and_default(config):
    from shotquill.config import SCROLL_MAX_HEIGHT_DEFAULT

    assert config.scrolling_max_height() == SCROLL_MAX_HEIGHT_DEFAULT
    config.set_scrolling_max_height(30_000)
    assert config.scrolling_max_height() == 30_000
    config.set_scrolling_max_height(40_000)
    assert config.scrolling_max_height() == 40_000
    config.set_scrolling_max_height(50_000)
    assert config.scrolling_max_height() == 50_000


def test_scrolling_max_height_unknown_value_falls_back(config):
    from shotquill.config import SCROLL_MAX_HEIGHT_DEFAULT

    config.set_scrolling_max_height(45_000)
    assert config.scrolling_max_height() == SCROLL_MAX_HEIGHT_DEFAULT


def test_config_persists_across_instances(config):
    from shotquill.config import Config

    config.set_language("zh")
    config.set_image_format("jpg")
    # A fresh wrapper reads the same backing store.
    reopened = Config()
    assert reopened.language() == "zh"
    assert reopened.image_format() == "jpg"


def test_editor_backdrop_defaults_on(config):
    from shotquill.config import DEFAULT_EDITOR_BACKDROP

    # The editor opens frameless over a dim backdrop (spotlight mode).
    assert DEFAULT_EDITOR_BACKDROP is True
    assert config.editor_backdrop() is True


def test_editor_backdrop_round_trip(config):
    config.set_editor_backdrop(False)
    assert config.editor_backdrop() is False
    config.set_editor_backdrop(True)
    assert config.editor_backdrop() is True


def test_toolbar_style_defaults_to_icon_only(config):
    from shotquill.config import DEFAULT_TOOLBAR_STYLE, TOOLBAR_STYLES

    assert DEFAULT_TOOLBAR_STYLE == "icon"
    assert DEFAULT_TOOLBAR_STYLE in TOOLBAR_STYLES
    assert config.toolbar_style() == DEFAULT_TOOLBAR_STYLE


def test_toolbar_style_round_trip(config):
    config.set_toolbar_style("icon")
    assert config.toolbar_style() == "icon"
    config.set_toolbar_style("text")
    assert config.toolbar_style() == "text"


def test_toolbar_style_unknown_value_falls_back(config):
    from shotquill.config import DEFAULT_TOOLBAR_STYLE

    # E.g. a hand-edited prefs file: read back as the default, not passed on
    # to the toolbar verbatim.
    config.set_toolbar_style("sideways")
    assert config.toolbar_style() == DEFAULT_TOOLBAR_STYLE
