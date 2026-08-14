# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
import string

import pytest

from shotquill import i18n


@pytest.fixture(autouse=True)
def _reset_language():
    i18n.set_language(i18n.DEFAULT_LANGUAGE)
    yield
    i18n.set_language(i18n.DEFAULT_LANGUAGE)


def test_default_language_is_english():
    assert i18n.DEFAULT_LANGUAGE == "en"
    assert i18n.current_language() == "en"


def test_every_string_has_all_languages():
    for key, entry in i18n._STRINGS.items():
        for lang in i18n.LANGUAGES:
            assert lang in entry, f"{key} missing {lang}"
            assert entry[lang], f"{key}.{lang} is empty"


def test_placeholder_fields_match_across_languages():
    # A translation that drops or renames a {placeholder} would raise KeyError
    # at .format() time in production — catch the drift here instead.
    def fields(template: str) -> set:
        return {field for _, field, _, _ in string.Formatter().parse(template) if field}

    for key, entry in i18n._STRINGS.items():
        reference = fields(entry[i18n.DEFAULT_LANGUAGE])
        for lang in i18n.LANGUAGES:
            assert fields(entry[lang]) == reference, f"{key}.{lang} placeholder mismatch"


def test_language_names_cover_every_language():
    # The Settings language picker is built from LANGUAGE_NAMES; a missing
    # entry would make a supported language unselectable.
    assert set(i18n.LANGUAGE_NAMES) == set(i18n.LANGUAGES)
    assert all(i18n.LANGUAGE_NAMES[lang] for lang in i18n.LANGUAGES)


def test_default_language_is_a_supported_language():
    assert i18n.DEFAULT_LANGUAGE in i18n.LANGUAGES


def test_translate_switches_language():
    assert i18n.t("menu.smart") == "Capture"
    assert i18n.t("toolbar.highlight") == "Highlight"
    i18n.set_language("zh")
    assert i18n.t("menu.smart") == "截图"
    assert i18n.t("toolbar.highlight") == "高亮"
    assert i18n.t("toolbar.font_size").strip() == "字号"


def test_unknown_key_returns_key():
    assert i18n.t("does.not.exist") == "does.not.exist"


def test_invalid_language_falls_back_to_default():
    i18n.set_language("fr")
    assert i18n.current_language() == "en"


def test_placeholder_templates_are_formattable():
    i18n.set_language("zh")
    assert i18n.t("title.ocr_copied").format(count=3) == "ShotQuill — 已复制 3 行文字"


def test_key_display_name_localizes_known_keys():
    assert i18n.key_display_name("Space") == "Space"
    assert i18n.key_display_name("Return") == "Enter"
    i18n.set_language("zh")
    assert i18n.key_display_name("Space") == "空格"
    assert i18n.key_display_name("Return") == "回车"


def test_key_display_name_keeps_modifier_prefix_and_unknown_keys():
    i18n.set_language("zh")
    # Only the final segment is localized; modifiers keep their Qt names.
    assert i18n.key_display_name("Ctrl+Return") == "Ctrl+回车"
    assert i18n.key_display_name("Ctrl+D") == "Ctrl+D"
    assert i18n.key_display_name("") == ""


def test_adjust_hint_key_picks_per_platform(monkeypatch):
    # Mac users see the keycap glyphs (⌥/⇧) they recognise from native apps;
    # everyone else gets the text variant so the hint doesn't look alien.
    monkeypatch.setattr(i18n.sys, "platform", "darwin")
    assert i18n.adjust_hint_key() == "editor.adjust_hint"
    monkeypatch.setattr(i18n.sys, "platform", "linux")
    assert i18n.adjust_hint_key() == "editor.adjust_hint_text"
    monkeypatch.setattr(i18n.sys, "platform", "win32")
    # Off-mac falls through to the text variant — Windows uses Alt/Shift too.
    assert i18n.adjust_hint_key() == "editor.adjust_hint_text"


def test_tray_unavailable_body_key_picks_per_platform(monkeypatch):
    # Linux gets the AppIndicator-extension pointer (the common GNOME 42+
    # stumble); macOS and Windows get the shorter generic body.
    monkeypatch.setattr(i18n.sys, "platform", "linux")
    assert i18n.tray_unavailable_body_key() == "tray.unavailable_body_linux"
    monkeypatch.setattr(i18n.sys, "platform", "darwin")
    assert i18n.tray_unavailable_body_key() == "tray.unavailable_body_generic"
    monkeypatch.setattr(i18n.sys, "platform", "win32")
    assert i18n.tray_unavailable_body_key() == "tray.unavailable_body_generic"
