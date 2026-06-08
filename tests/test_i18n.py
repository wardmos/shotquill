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
    i18n.set_language("zh")
    assert i18n.t("menu.smart") == "截图"


def test_unknown_key_returns_key():
    assert i18n.t("does.not.exist") == "does.not.exist"


def test_invalid_language_falls_back_to_default():
    i18n.set_language("fr")
    assert i18n.current_language() == "en"


def test_placeholder_templates_are_formattable():
    i18n.set_language("zh")
    assert i18n.t("title.saved").format(name="a.png") == "ShotQuill — 已保存 a.png"


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
