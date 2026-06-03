# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
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


def test_translate_switches_language():
    assert i18n.t("menu.smart") == "Smart Capture"
    i18n.set_language("zh")
    assert i18n.t("menu.smart") == "智能截图"


def test_unknown_key_returns_key():
    assert i18n.t("does.not.exist") == "does.not.exist"


def test_invalid_language_falls_back_to_default():
    i18n.set_language("fr")
    assert i18n.current_language() == "en"


def test_placeholder_templates_are_formattable():
    i18n.set_language("zh")
    assert i18n.t("title.saved").format(name="a.png") == "ShotQuill — 已保存 a.png"
