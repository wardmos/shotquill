# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Headless tests for the settings dialog and its hotkey-row widget."""

import pytest

pytest.importorskip("PySide6")

from shotquill.ui.settings import SettingsDialog, _HotkeyRow  # noqa: E402


def test_hotkey_row_round_trips_a_combo(qtbot):
    row = _HotkeyRow("<cmd>+<shift>+a")
    qtbot.addWidget(row)
    assert row.combo() == "<cmd>+<shift>+a"


def test_hotkey_row_reflects_modifiers(qtbot):
    row = _HotkeyRow("<alt>+s")
    qtbot.addWidget(row)
    assert row._alt.isChecked() is True
    assert row._cmd.isChecked() is False
    assert row._key.currentText().lower() == "s"


def test_dialog_prefills_from_config(qtbot, config):
    config.set_language("zh")
    config.set_image_format("jpg")
    config.set_save_dir("/tmp/shots")
    config.set_autostart(True)

    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    assert dialog._language.currentData() == "zh"
    assert dialog._format.currentText() == "jpg"
    assert dialog._save_dir.text() == "/tmp/shots"
    assert dialog._autostart.isChecked() is True


def test_dialog_save_writes_back_to_config(qtbot, config):
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)

    dialog._save_dir.setText("/tmp/new")
    fmt_index = dialog._format.findText("jpg")
    dialog._format.setCurrentIndex(fmt_index)
    lang_index = dialog._language.findData("zh")
    dialog._language.setCurrentIndex(lang_index)
    dialog._flash.setChecked(False)
    dialog._sound.setChecked(True)
    dialog._save_and_accept()

    assert config.save_dir() == "/tmp/new"
    assert config.image_format() == "jpg"
    assert config.language() == "zh"
    assert config.flash_on_capture() is False
    assert config.sound_on_capture() is True


def test_dialog_save_persists_custom_hotkey(qtbot, config):
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    dialog._region._cmd.setChecked(True)
    dialog._region._alt.setChecked(False)
    key_index = dialog._region._key.findText("R")
    dialog._region._key.setCurrentIndex(key_index)
    dialog._save_and_accept()
    assert config.hotkey("region_capture") == "<cmd>+r"
