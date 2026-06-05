# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Settings dialog: language, save directory, image format, and custom hotkeys."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from shotquill.hotkeys.combo import parse_combo, to_pynput_combo
from shotquill.i18n import LANGUAGE_NAMES, LANGUAGES, t
from shotquill.ui.toolbar import RESERVED_SHORTCUTS

if TYPE_CHECKING:
    from shotquill.config import Config

_KEYS = [*"abcdefghijklmnopqrstuvwxyz", *"0123456789", *(f"f{i}" for i in range(1, 13))]
_FORMATS = ["png", "jpg"]


# pynput modifiers → the Qt portable names Qt reports for the same physical
# keys on macOS (where Qt swaps Ctrl and Cmd; the hotkey manager is Mac-only).
_PYNPUT_TO_QT_MODIFIER = {"cmd": "Ctrl", "ctrl": "Meta", "alt": "Alt", "shift": "Shift"}


def _capture_combo_sequence(combo: str) -> QKeySequence:
    """The QKeySequence Qt would deliver for a pynput capture combo.

    Used to refuse finish keys that would double-fire with a global capture
    hotkey: the pynput listener fires regardless of focus, so the same press
    would take a screenshot *and* run the editor action.
    """
    parsed = parse_combo(combo)
    parts = [qt for mod, qt in _PYNPUT_TO_QT_MODIFIER.items() if parsed[mod]]
    parts.append(str(parsed["key"]).upper())
    return QKeySequence("+".join(parts))


def _reserved_editor_sequences() -> list[QKeySequence]:
    """Key combos the editor already binds (toolbar's RESERVED_SHORTCUTS and Esc).

    A finish key set to one of these would be shadowed: Qt's shortcut system
    consumes the press before EditorWindow.keyPressEvent ever sees it, so the
    Settings dialog refuses them. ``keyBindings`` covers every platform binding
    of each standard key (e.g. both Ctrl+C and Ctrl+Insert for Copy on Linux).
    """
    reserved = [binding for key in RESERVED_SHORTCUTS for binding in QKeySequence.keyBindings(key)]
    reserved.append(QKeySequence(Qt.Key_Escape))
    return reserved


class _HotkeyRow(QWidget):
    """An enable toggle, ⌘/⌃/⌥/⇧ checkboxes, and a key dropdown.

    Yields a pynput combo string plus whether the hotkey is enabled. When the
    enable box is unchecked the combo controls are greyed out to make it clear
    the shortcut is off.
    """

    def __init__(self, combo: str, enabled: bool = True) -> None:
        super().__init__()
        parsed = parse_combo(combo)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._enabled = QCheckBox(t("settings.hotkey_enabled"))
        self._enabled.setChecked(enabled)

        self._cmd = QCheckBox("⌘")
        self._ctrl = QCheckBox("⌃")
        self._alt = QCheckBox("⌥")
        self._shift = QCheckBox("⇧")
        self._cmd.setChecked(bool(parsed["cmd"]))
        self._ctrl.setChecked(bool(parsed["ctrl"]))
        self._alt.setChecked(bool(parsed["alt"]))
        self._shift.setChecked(bool(parsed["shift"]))

        self._key = QComboBox()
        self._key.addItems([k.upper() for k in _KEYS])
        index = self._key.findText(str(parsed["key"]).upper())
        if index >= 0:
            self._key.setCurrentIndex(index)

        self._controls = (self._cmd, self._ctrl, self._alt, self._shift, self._key)
        layout.addWidget(self._enabled)
        for widget in self._controls:
            layout.addWidget(widget)
        layout.addStretch()

        self._enabled.toggled.connect(self._sync_enabled)
        self._sync_enabled(self._enabled.isChecked())

    def _sync_enabled(self, on: bool) -> None:
        for widget in self._controls:
            widget.setEnabled(on)

    def enabled(self) -> bool:
        return self._enabled.isChecked()

    def combo(self) -> str:
        return to_pynput_combo(
            cmd=self._cmd.isChecked(),
            ctrl=self._ctrl.isChecked(),
            alt=self._alt.isChecked(),
            shift=self._shift.isChecked(),
            key=self._key.currentText(),
        )


class _EditorKeyRow(QWidget):
    """An enable toggle plus a key recorder for an in-editor finish key.

    Yields a QKeySequence portable string (e.g. ``Space``, ``Ctrl+Return``) plus
    whether the key is enabled. When the enable box is unchecked the recorder is
    greyed out to make it clear the shortcut is off.
    """

    def __init__(self, sequence: str, enabled: bool = True) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._enabled = QCheckBox(t("settings.hotkey_enabled"))
        self._enabled.setChecked(enabled)

        self._edit = QKeySequenceEdit(QKeySequence(sequence))
        self._edit.setMaximumSequenceLength(1)  # a single chord, not a multi-key chain

        layout.addWidget(self._enabled)
        layout.addWidget(self._edit)
        layout.addStretch()

        self._enabled.toggled.connect(self._edit.setEnabled)
        self._edit.setEnabled(self._enabled.isChecked())

    def enabled(self) -> bool:
        return self._enabled.isChecked()

    def sequence(self) -> str:
        return self._edit.keySequence().toString()

    def active_sequence(self) -> QKeySequence:
        """The recorded sequence, or an empty one when the key is disabled."""
        return self._edit.keySequence() if self.enabled() else QKeySequence()


class SettingsDialog(QDialog):
    def __init__(self, config: Config) -> None:
        super().__init__()
        self._config = config
        self.setWindowTitle(t("settings.title"))

        form = QFormLayout()

        self._language = QComboBox()
        for code in LANGUAGES:
            self._language.addItem(LANGUAGE_NAMES[code], code)
        lang_index = self._language.findData(config.language())
        if lang_index >= 0:
            self._language.setCurrentIndex(lang_index)
        form.addRow(t("settings.language"), self._language)

        self._save_dir = QLineEdit(config.save_dir())
        browse = QPushButton(t("settings.browse"))
        browse.clicked.connect(self._browse)
        dir_row = QWidget()
        dir_layout = QHBoxLayout(dir_row)
        dir_layout.setContentsMargins(0, 0, 0, 0)
        dir_layout.addWidget(self._save_dir)
        dir_layout.addWidget(browse)
        form.addRow(t("settings.save_dir"), dir_row)

        self._format = QComboBox()
        self._format.addItems(_FORMATS)
        format_index = self._format.findText(config.image_format().lower())
        if format_index >= 0:
            self._format.setCurrentIndex(format_index)
        form.addRow(t("settings.format"), self._format)

        self._smart = _HotkeyRow(
            config.hotkey("smart_capture"), config.hotkey_enabled("smart_capture")
        )
        self._fullscreen = _HotkeyRow(
            config.hotkey("fullscreen_capture"), config.hotkey_enabled("fullscreen_capture")
        )
        form.addRow(t("settings.smart"), self._smart)
        form.addRow(t("settings.fullscreen"), self._fullscreen)

        self._editor_copy = _EditorKeyRow(
            config.editor_hotkey("editor_copy"), config.hotkey_enabled("editor_copy")
        )
        self._editor_save = _EditorKeyRow(
            config.editor_hotkey("editor_save"), config.hotkey_enabled("editor_save")
        )
        form.addRow(t("settings.editor_copy"), self._editor_copy)
        form.addRow(t("settings.editor_save"), self._editor_save)

        self._auto_save = QCheckBox(t("settings.auto_save"))
        self._auto_save.setChecked(config.auto_save_after_capture())
        form.addRow(t("settings.auto_output"), self._auto_save)

        self._auto_copy = QCheckBox(t("settings.auto_copy"))
        self._auto_copy.setChecked(config.auto_copy_after_capture())
        form.addRow("", self._auto_copy)

        self._include_cursor = QCheckBox(t("settings.include_cursor"))
        self._include_cursor.setChecked(config.include_cursor())
        form.addRow("", self._include_cursor)

        self._autostart = QCheckBox(t("settings.autostart"))
        self._autostart.setChecked(config.autostart())
        form.addRow("", self._autostart)

        self._flash = QCheckBox(t("settings.flash"))
        self._flash.setChecked(config.flash_on_capture())
        form.addRow("", self._flash)

        self._sound = QCheckBox(t("settings.sound"))
        self._sound.setChecked(config.sound_on_capture())
        form.addRow("", self._sound)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, t("settings.choose_dir"), self._save_dir.text()
        )
        if path:
            self._save_dir.setText(path)

    def _validate_editor_keys(self) -> bool:
        """Refuse finish keys that collide with built-in editor shortcuts,
        with each other, or with an enabled global capture hotkey (using the
        capture rows' pending values, so both kinds can change in one visit).
        An enabled row with no key recorded is also refused — it would look
        active in Settings yet silently never fire."""
        for row in (self._editor_copy, self._editor_save):
            if row.enabled() and row.active_sequence().isEmpty():
                QMessageBox.warning(self, t("settings.title"), t("settings.editor_key_empty"))
                return False
        copy_seq = self._editor_copy.active_sequence()
        save_seq = self._editor_save.active_sequence()
        reserved = _reserved_editor_sequences()
        captures = [
            _capture_combo_sequence(row.combo())
            for row in (self._smart, self._fullscreen)
            if row.enabled()
        ]
        for sequence in (copy_seq, save_seq):
            if sequence.isEmpty():
                continue
            if sequence in reserved:
                QMessageBox.warning(self, t("settings.title"), t("settings.editor_key_conflict"))
                return False
            if sequence in captures:
                QMessageBox.warning(
                    self, t("settings.title"), t("settings.editor_key_capture_conflict")
                )
                return False
        if not copy_seq.isEmpty() and copy_seq == save_seq:
            QMessageBox.warning(self, t("settings.title"), t("settings.editor_key_duplicate"))
            return False
        return True

    def _save_and_accept(self) -> None:
        if not self._validate_editor_keys():
            return  # keep the dialog open so the user can pick another key
        self._config.set_language(self._language.currentData())
        self._config.set_save_dir(self._save_dir.text())
        self._config.set_image_format(self._format.currentText())
        self._config.set_hotkey("smart_capture", self._smart.combo())
        self._config.set_hotkey("fullscreen_capture", self._fullscreen.combo())
        self._config.set_hotkey_enabled("smart_capture", self._smart.enabled())
        self._config.set_hotkey_enabled("fullscreen_capture", self._fullscreen.enabled())
        self._config.set_editor_hotkey("editor_copy", self._editor_copy.sequence())
        self._config.set_editor_hotkey("editor_save", self._editor_save.sequence())
        self._config.set_hotkey_enabled("editor_copy", self._editor_copy.enabled())
        self._config.set_hotkey_enabled("editor_save", self._editor_save.enabled())
        self._config.set_auto_save_after_capture(self._auto_save.isChecked())
        self._config.set_auto_copy_after_capture(self._auto_copy.isChecked())
        self._config.set_include_cursor(self._include_cursor.isChecked())
        self._config.set_autostart(self._autostart.isChecked())
        self._config.set_flash_on_capture(self._flash.isChecked())
        self._config.set_sound_on_capture(self._sound.isChecked())
        self.accept()
