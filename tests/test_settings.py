# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Headless tests for the settings dialog and its hotkey-row widget."""

import pytest

pytest.importorskip("PySide6")

from shotquill.ui.settings import SettingsDialog, _EditorKeyRow, _HotkeyRow  # noqa: E402


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


def test_editor_key_row_round_trips_a_sequence(qtbot):
    row = _EditorKeyRow("Ctrl+Return")
    qtbot.addWidget(row)
    assert row.sequence() == "Ctrl+Return"
    assert row.enabled() is True


def test_editor_key_row_disabled_greys_out_recorder(qtbot):
    row = _EditorKeyRow("Space", enabled=False)
    qtbot.addWidget(row)
    assert row.enabled() is False
    assert row._edit.isEnabled() is False
    # Re-enabling restores the recorder.
    row._enabled.setChecked(True)
    assert row._edit.isEnabled() is True


def test_dialog_prefills_editor_keys_from_config(qtbot, config):
    config.set_editor_hotkey("editor_copy", "Ctrl+D")
    config.set_hotkey_enabled("editor_save", False)
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    assert dialog._editor_copy.sequence() == "Ctrl+D"
    assert dialog._editor_save.sequence() == "Return"
    assert dialog._editor_save.enabled() is False


def test_dialog_save_persists_editor_keys(qtbot, config):
    from PySide6.QtGui import QKeySequence

    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    dialog._editor_copy._edit.setKeySequence(QKeySequence("Ctrl+D"))
    dialog._editor_save._enabled.setChecked(False)
    dialog._save_and_accept()
    assert config.editor_hotkey("editor_copy") == "Ctrl+D"
    assert config.hotkey_enabled("editor_copy") is True
    assert config.editor_hotkey("editor_save") == "Return"
    assert config.hotkey_enabled("editor_save") is False


def test_dialog_prefills_hover_switch_from_config(qtbot, config):
    from shotquill.config import DEFAULT_HOVER_SWITCH_DELAY_MS, HOVER_SWITCH_NEVER

    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    assert dialog._hover_switch.currentData() == DEFAULT_HOVER_SWITCH_DELAY_MS

    # A value set outside the dialog (hand-edited prefs) stays selectable.
    config.set_hover_switch_delay_ms(1234)
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    assert dialog._hover_switch.currentData() == 1234

    config.set_hover_switch_delay_ms(HOVER_SWITCH_NEVER)
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    assert dialog._hover_switch.currentData() == HOVER_SWITCH_NEVER


def test_dialog_save_persists_hover_switch(qtbot, config):
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    dialog._hover_switch.setCurrentIndex(dialog._hover_switch.findData(0))
    dialog._save_and_accept()
    assert config.hover_switch_delay_ms() == 0


def test_dialog_prefills_region_adjust_from_config(qtbot, config):
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    assert dialog._region_adjust.isChecked() is True  # on by default

    config.set_region_adjust(False)
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    assert dialog._region_adjust.isChecked() is False


def test_dialog_save_persists_region_adjust(qtbot, config):
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    dialog._region_adjust.setChecked(False)
    dialog._save_and_accept()
    assert config.region_adjust() is False


def test_dialog_prefills_editor_backdrop_from_config(qtbot, config):
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    assert dialog._editor_backdrop.isChecked() is True  # on by default

    config.set_editor_backdrop(False)
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    assert dialog._editor_backdrop.isChecked() is False


def test_dialog_save_persists_editor_backdrop(qtbot, config):
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    dialog._editor_backdrop.setChecked(False)
    dialog._save_and_accept()
    assert config.editor_backdrop() is False


def test_dialog_prefills_toolbar_style_from_config(qtbot, config):
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    assert dialog._toolbar_style.currentData() == "icon"  # icon-only by default

    config.set_toolbar_style("both")
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    assert dialog._toolbar_style.currentData() == "both"


def test_dialog_save_persists_toolbar_style(qtbot, config):
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    dialog._toolbar_style.setCurrentIndex(dialog._toolbar_style.findData("text"))
    dialog._save_and_accept()
    assert config.toolbar_style() == "text"


def _silence_warnings(monkeypatch):
    """Swallow the QMessageBox.warning popup (it would block offscreen tests)."""
    from PySide6.QtWidgets import QMessageBox

    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *args, **kwargs: warnings.append(args))
    )
    return warnings


def test_dialog_rejects_finish_key_reserved_by_toolbar(qtbot, config, monkeypatch):
    from PySide6.QtGui import QKeySequence

    warnings = _silence_warnings(monkeypatch)
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    # Ctrl+C (QKeySequence.Copy) is bound by the toolbar's Copy action: the
    # shortcut system would consume it before keyPressEvent, so it's refused.
    dialog._editor_copy._edit.setKeySequence(QKeySequence(QKeySequence.Copy))
    dialog._save_and_accept()
    assert len(warnings) == 1
    assert dialog.result() != SettingsDialog.Accepted
    assert config.editor_hotkey("editor_copy") == "Space"  # unchanged


def test_dialog_rejects_same_key_for_copy_and_save(qtbot, config, monkeypatch):
    from PySide6.QtGui import QKeySequence

    warnings = _silence_warnings(monkeypatch)
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    dialog._editor_copy._edit.setKeySequence(QKeySequence("Ctrl+D"))
    dialog._editor_save._edit.setKeySequence(QKeySequence("Ctrl+D"))
    dialog._save_and_accept()
    assert len(warnings) == 1
    assert dialog.result() != SettingsDialog.Accepted


def test_dialog_rejects_same_combo_for_both_capture_hotkeys(qtbot, config, monkeypatch):
    # The hotkey manager keys bindings by combo string, so identical combos
    # would let the later registration silently replace the earlier one.
    warnings = _silence_warnings(monkeypatch)
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    # Point fullscreen at smart capture's default <alt>+a.
    key_index = dialog._fullscreen._key.findText("A")
    dialog._fullscreen._key.setCurrentIndex(key_index)
    dialog._save_and_accept()
    assert len(warnings) == 1
    assert dialog.result() != SettingsDialog.Accepted
    assert config.hotkey("fullscreen_capture") == "<alt>+s"  # unchanged


def test_dialog_allows_same_capture_combo_when_one_is_disabled(qtbot, config, monkeypatch):
    # A disabled hotkey never registers, so sharing its combo is harmless.
    warnings = _silence_warnings(monkeypatch)
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    key_index = dialog._fullscreen._key.findText("A")
    dialog._fullscreen._key.setCurrentIndex(key_index)
    dialog._fullscreen._enabled.setChecked(False)
    dialog._save_and_accept()
    assert warnings == []
    assert dialog.result() == SettingsDialog.Accepted
    assert config.hotkey("fullscreen_capture") == "<alt>+a"
    assert config.hotkey_enabled("fullscreen_capture") is False


def test_capture_combo_sequence_maps_macos_modifiers(qtbot):
    from PySide6.QtGui import QKeySequence

    from shotquill.ui.settings import _capture_combo_sequence

    # Qt swaps Ctrl/Cmd on macOS: pynput <cmd> arrives as Qt "Ctrl", <ctrl> as "Meta".
    assert _capture_combo_sequence("<alt>+a") == QKeySequence("Alt+A")
    assert _capture_combo_sequence("<cmd>+<shift>+r") == QKeySequence("Ctrl+Shift+R")
    assert _capture_combo_sequence("<ctrl>+1") == QKeySequence("Meta+1")


def test_dialog_rejects_finish_key_matching_capture_hotkey(qtbot, config, monkeypatch):
    from PySide6.QtGui import QKeySequence

    warnings = _silence_warnings(monkeypatch)
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    # Default smart-capture hotkey is <alt>+a; Qt delivers that press as Alt+A,
    # so a finish key set to it would screenshot AND copy at once.
    dialog._editor_copy._edit.setKeySequence(QKeySequence("Alt+A"))
    dialog._save_and_accept()
    assert len(warnings) == 1
    assert dialog.result() != SettingsDialog.Accepted
    assert config.editor_hotkey("editor_copy") == "Space"  # unchanged


def test_dialog_allows_capture_combo_when_that_capture_is_disabled(qtbot, config, monkeypatch):
    from PySide6.QtGui import QKeySequence

    warnings = _silence_warnings(monkeypatch)
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    # Disabling the smart-capture hotkey in the same visit frees its combo.
    dialog._smart._enabled.setChecked(False)
    dialog._editor_copy._edit.setKeySequence(QKeySequence("Alt+A"))
    dialog._save_and_accept()
    assert warnings == []
    assert dialog.result() == SettingsDialog.Accepted
    assert config.editor_hotkey("editor_copy") == "Alt+A"


def test_dialog_allows_reserved_key_on_disabled_row(qtbot, config, monkeypatch):
    from PySide6.QtGui import QKeySequence

    warnings = _silence_warnings(monkeypatch)
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    # A disabled row never fires, so a colliding combo there is harmless.
    dialog._editor_copy._edit.setKeySequence(QKeySequence(QKeySequence.Copy))
    dialog._editor_copy._enabled.setChecked(False)
    dialog._save_and_accept()
    assert warnings == []
    assert dialog.result() == SettingsDialog.Accepted
    assert config.hotkey_enabled("editor_copy") is False


def test_dialog_rejects_enabled_row_with_no_key_recorded(qtbot, config, monkeypatch):
    from PySide6.QtGui import QKeySequence

    warnings = _silence_warnings(monkeypatch)
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    # Enabled but empty would look active in Settings yet never fire.
    dialog._editor_copy._edit.setKeySequence(QKeySequence())
    dialog._save_and_accept()
    assert len(warnings) == 1
    assert dialog.result() != SettingsDialog.Accepted
    assert config.editor_hotkey("editor_copy") == "Space"  # unchanged


def test_dialog_allows_empty_key_on_disabled_row(qtbot, config, monkeypatch):
    from PySide6.QtGui import QKeySequence

    warnings = _silence_warnings(monkeypatch)
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    dialog._editor_copy._edit.setKeySequence(QKeySequence())
    dialog._editor_copy._enabled.setChecked(False)
    dialog._save_and_accept()
    assert warnings == []
    assert dialog.result() == SettingsDialog.Accepted
    assert config.hotkey_enabled("editor_copy") is False


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


def test_is_usable_save_dir_accepts_existing_writable_dir(tmp_path):
    from shotquill.ui.settings import _is_usable_save_dir

    assert _is_usable_save_dir(str(tmp_path)) is True


def test_is_usable_save_dir_accepts_creatable_path(tmp_path):
    from shotquill.ui.settings import _is_usable_save_dir

    # Doesn't exist yet, but the saver can mkdir it under the writable tmp dir.
    assert _is_usable_save_dir(str(tmp_path / "shots" / "2026")) is True


def test_is_usable_save_dir_rejects_empty_and_whitespace():
    from shotquill.ui.settings import _is_usable_save_dir

    assert _is_usable_save_dir("") is False
    assert _is_usable_save_dir("   ") is False


def test_is_usable_save_dir_rejects_a_file_path(tmp_path):
    from shotquill.ui.settings import _is_usable_save_dir

    file_path = tmp_path / "shot.png"
    file_path.touch()
    assert _is_usable_save_dir(str(file_path)) is False


def test_is_usable_save_dir_rejects_unwritable_dir(tmp_path):
    import os

    from shotquill.ui.settings import _is_usable_save_dir

    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    try:
        if os.access(locked, os.W_OK):  # e.g. running as root
            pytest.skip("cannot create an unwritable directory here")
        assert _is_usable_save_dir(str(locked)) is False
    finally:
        locked.chmod(0o755)


def test_dialog_rejects_invalid_save_dir(qtbot, config, monkeypatch, tmp_path):
    warnings = _silence_warnings(monkeypatch)
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    original = config.save_dir()
    dialog._save_dir.setText("")
    dialog._save_and_accept()
    assert len(warnings) == 1
    assert dialog.result() != SettingsDialog.Accepted
    assert config.save_dir() == original  # unchanged


def test_dialog_accepts_valid_save_dir(qtbot, config, tmp_path):
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    dialog._save_dir.setText(str(tmp_path / "shots"))
    dialog._save_and_accept()
    assert dialog.result() == SettingsDialog.Accepted
    assert config.save_dir() == str(tmp_path / "shots")


def test_permission_rows_show_status_and_open_the_right_pane(qtbot, config, monkeypatch):
    from PySide6.QtWidgets import QPushButton

    from shotquill import permissions
    from shotquill.i18n import t
    from shotquill.permissions import PermissionStatus
    from shotquill.ui import settings as settings_module

    # The permission rows only render on macOS (the only platform with these
    # TCC grants). Pin sys.platform so the test exercises that branch on any
    # host — otherwise the rows would be hidden on a Linux CI box and the
    # ``_screen_permission`` attr would be ``None``.
    monkeypatch.setattr(settings_module.sys, "platform", "darwin")

    opened = []
    monkeypatch.setattr(permissions, "screen_capture_status", lambda: PermissionStatus.GRANTED)
    monkeypatch.setattr(permissions, "input_monitoring_status", lambda: PermissionStatus.DENIED)
    monkeypatch.setattr(permissions, "open_screen_capture_pane", lambda: opened.append("screen"))
    monkeypatch.setattr(permissions, "open_input_monitoring_pane", lambda: opened.append("input"))

    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    assert dialog._screen_permission._label.text() == t("settings.permission_granted")
    assert dialog._input_permission._label.text() == t("settings.permission_denied")

    dialog._screen_permission.findChild(QPushButton).click()
    dialog._input_permission.findChild(QPushButton).click()
    assert opened == ["screen", "input"]


def test_permission_rows_show_unknown_when_state_is_unreadable(qtbot, config, monkeypatch):
    from shotquill import permissions
    from shotquill.i18n import t
    from shotquill.permissions import PermissionStatus
    from shotquill.ui import settings as settings_module

    monkeypatch.setattr(settings_module.sys, "platform", "darwin")
    monkeypatch.setattr(permissions, "screen_capture_status", lambda: PermissionStatus.UNKNOWN)
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    assert dialog._screen_permission._label.text() == t("settings.permission_unknown")


def test_permission_rows_refresh_when_dialog_reactivates(qtbot, config, monkeypatch):
    # Granting happens over in System Settings; coming back must re-read the
    # state without closing and reopening the dialog.
    from PySide6.QtCore import QEvent

    from shotquill import permissions
    from shotquill.i18n import t
    from shotquill.permissions import PermissionStatus
    from shotquill.ui import settings as settings_module

    monkeypatch.setattr(settings_module.sys, "platform", "darwin")
    status = [PermissionStatus.DENIED]
    monkeypatch.setattr(permissions, "screen_capture_status", lambda: status[0])
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    assert dialog._screen_permission._label.text() == t("settings.permission_denied")

    status[0] = PermissionStatus.GRANTED
    monkeypatch.setattr(dialog, "isActiveWindow", lambda: True)
    dialog.changeEvent(QEvent(QEvent.ActivationChange))
    assert dialog._screen_permission._label.text() == t("settings.permission_granted")


def test_permission_rows_hidden_on_linux(qtbot, config, monkeypatch):
    # Linux has no equivalent of macOS Screen Recording / Input Monitoring
    # grants — the rows would surface meaningless "Unknown" status plus an
    # "Open System Settings" button that would shell out to a macOS-only
    # `x-apple-systempreferences:` URL. They must not appear in the dialog.
    from shotquill.ui import settings as settings_module

    monkeypatch.setattr(settings_module.sys, "platform", "linux")
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    assert dialog._screen_permission is None
    assert dialog._input_permission is None


def test_permission_changeEvent_is_a_noop_on_linux(qtbot, config, monkeypatch):
    # Without the rows there's nothing to refresh on activation — make sure
    # the activation handler doesn't try to dereference ``None`` (the
    # original ``self._screen_permission.refresh()`` call would crash).
    from PySide6.QtCore import QEvent

    from shotquill.ui import settings as settings_module

    monkeypatch.setattr(settings_module.sys, "platform", "linux")
    dialog = SettingsDialog(config)
    qtbot.addWidget(dialog)
    monkeypatch.setattr(dialog, "isActiveWindow", lambda: True)
    # Must not raise.
    dialog.changeEvent(QEvent(QEvent.ActivationChange))
