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


def test_hotkey_row_disabled_greys_out_controls(qtbot):
    row = _HotkeyRow("<alt>+a", enabled=False)
    qtbot.addWidget(row)
    assert row.enabled() is False
    assert row._alt.isEnabled() is False
    assert row._key.isEnabled() is False
    # Re-enabling restores the combo controls.
    row._enabled.setChecked(True)
    assert row._alt.isEnabled() is True
    assert row._key.isEnabled() is True


def test_dialog_prefills_from_config(qtbot, config):
    config.set_language("zh")
    config.set_image_format("jpg")
    config.set_save_dir("/tmp/shots")
    config.set_autostart(True)
    config.set_include_cursor(True)

    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    assert dialog._language.currentData() == "zh"
    assert dialog._format.currentText() == "jpg"
    assert dialog._save_dir.text() == "/tmp/shots"
    assert dialog._autostart.isChecked() is True
    assert dialog._include_cursor.isChecked() is True


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
    dialog._include_cursor.setChecked(True)
    dialog._save_and_accept()

    assert config.save_dir() == "/tmp/new"
    assert config.image_format() == "jpg"
    assert config.language() == "zh"
    assert config.flash_on_capture() is False
    assert config.sound_on_capture() is True
    assert config.include_cursor() is True


def test_dialog_save_persists_custom_hotkey(qtbot, config):
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    dialog._smart._cmd.setChecked(True)
    dialog._smart._alt.setChecked(False)
    key_index = dialog._smart._key.findText("R")
    dialog._smart._key.setCurrentIndex(key_index)
    dialog._save_and_accept()
    assert config.hotkey("smart_capture") == "<cmd>+r"


def test_dialog_prefills_hotkey_enabled_state(qtbot, config):
    config.set_hotkey_enabled("fullscreen_capture", False)
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    assert dialog._smart.enabled() is True
    assert dialog._fullscreen.enabled() is False


def test_dialog_save_persists_hotkey_enabled_state(qtbot, config):
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    dialog._smart._enabled.setChecked(False)
    dialog._save_and_accept()
    assert config.hotkey_enabled("smart_capture") is False
    assert config.hotkey_enabled("fullscreen_capture") is True
