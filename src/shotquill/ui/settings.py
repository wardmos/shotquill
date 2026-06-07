# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Settings dialog: language, save directory, image format, and custom hotkeys."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, Qt
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
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from shotquill import permissions
from shotquill.config import HOVER_SWITCH_NEVER
from shotquill.hotkeys.combo import parse_combo, to_pynput_combo
from shotquill.i18n import LANGUAGE_NAMES, LANGUAGES, t
from shotquill.permissions import PermissionStatus
from shotquill.ui.toolbar import RESERVED_SHORTCUTS

if TYPE_CHECKING:
    from collections.abc import Callable

    from shotquill.config import Config

_KEYS = [*"abcdefghijklmnopqrstuvwxyz", *"0123456789", *(f"f{i}" for i in range(1, 13))]
_FORMATS = ["png", "jpg"]
# Overlay highlight-switch delay choices (ms); HOVER_SWITCH_NEVER means the
# highlight only ever moves when a window is clicked.
_HOVER_SWITCH_CHOICES = [0, 500, 1000, 3000, 5000, HOVER_SWITCH_NEVER]


def _hover_switch_label(delay_ms: int) -> str:
    if delay_ms == HOVER_SWITCH_NEVER:
        return t("settings.hover_switch_never")
    if delay_ms == 0:
        return t("settings.hover_switch_instant")
    return t("settings.hover_switch_seconds").format(seconds=f"{delay_ms / 1000:g}")


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


def _is_usable_save_dir(text: str) -> bool:
    """Whether ``text`` names a directory a capture could be saved into.

    Accepts an existing writable directory, or a path that the saver could
    create on demand (its nearest existing ancestor is a writable directory).
    Auto-save is on by default, so a folder that can't take a file would
    otherwise only surface as a failure on the next capture.
    """
    if not text.strip():
        return False
    probe = Path(text.strip()).expanduser()
    while not probe.exists():
        parent = probe.parent
        if parent == probe:  # ran out of ancestors (nonexistent root/anchor)
            return False
        probe = parent
    return probe.is_dir() and os.access(probe, os.W_OK)


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


# Status text key and colour per permission state. DENIED is the case worth
# shouting about; UNKNOWN (can't read the state) stays muted, not alarming.
_PERMISSION_LABELS = {
    PermissionStatus.GRANTED: ("settings.permission_granted", "#2e9e44"),
    PermissionStatus.DENIED: ("settings.permission_denied", "#d04545"),
    PermissionStatus.UNKNOWN: ("settings.permission_unknown", "#888888"),
}


class _PermissionRow(QWidget):
    """Live status of one macOS privacy permission plus a System Settings link.

    The permission itself can only be granted in System Settings, so the row
    is read-only feedback: a coloured status label and a button that deep-links
    to the right privacy pane. ``refresh()`` re-reads the state — the dialog
    calls it when it regains focus, so a grant made in System Settings shows
    up as soon as the user comes back.
    """

    def __init__(
        self, status: Callable[[], PermissionStatus], open_pane: Callable[[], None]
    ) -> None:
        super().__init__()
        self._status = status
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._label = QLabel()
        open_button = QPushButton(t("settings.open_system_settings"))
        open_button.clicked.connect(open_pane)

        layout.addWidget(self._label)
        layout.addWidget(open_button)
        layout.addStretch()
        self.refresh()

    def refresh(self) -> None:
        key, color = _PERMISSION_LABELS[self._status()]
        self._label.setText(t(key))
        self._label.setStyleSheet(f"color: {color};")


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

        self._hover_switch = QComboBox()
        for choice in _HOVER_SWITCH_CHOICES:
            self._hover_switch.addItem(_hover_switch_label(choice), choice)
        delay = config.hover_switch_delay_ms()
        delay_index = self._hover_switch.findData(delay)
        if delay_index < 0:  # a value set outside the dialog: keep it selectable
            self._hover_switch.addItem(_hover_switch_label(delay), delay)
            delay_index = self._hover_switch.count() - 1
        self._hover_switch.setCurrentIndex(delay_index)
        form.addRow(t("settings.hover_switch"), self._hover_switch)

        self._region_adjust = QCheckBox(t("settings.region_adjust"))
        self._region_adjust.setChecked(config.region_adjust())
        form.addRow("", self._region_adjust)

        self._editor_backdrop = QCheckBox(t("settings.editor_backdrop"))
        self._editor_backdrop.setChecked(config.editor_backdrop())
        form.addRow("", self._editor_backdrop)

        self._autostart = QCheckBox(t("settings.autostart"))
        self._autostart.setChecked(config.autostart())
        form.addRow("", self._autostart)

        self._flash = QCheckBox(t("settings.flash"))
        self._flash.setChecked(config.flash_on_capture())
        form.addRow("", self._flash)

        self._sound = QCheckBox(t("settings.sound"))
        self._sound.setChecked(config.sound_on_capture())
        form.addRow("", self._sound)

        self._screen_permission = _PermissionRow(
            permissions.screen_capture_status, permissions.open_screen_capture_pane
        )
        self._input_permission = _PermissionRow(
            permissions.input_monitoring_status, permissions.open_input_monitoring_pane
        )
        form.addRow(t("settings.permission_screen"), self._screen_permission)
        form.addRow(t("settings.permission_input"), self._input_permission)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def changeEvent(self, event) -> None:
        # Granting a permission happens over in System Settings; coming back
        # re-activates this dialog, so that's the moment to re-read the states.
        if event.type() == QEvent.ActivationChange and self.isActiveWindow():
            self._screen_permission.refresh()
            self._input_permission.refresh()
        super().changeEvent(event)

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, t("settings.choose_dir"), self._save_dir.text()
        )
        if path:
            self._save_dir.setText(path)

    def _validate_save_dir(self) -> bool:
        if not _is_usable_save_dir(self._save_dir.text()):
            QMessageBox.warning(self, t("settings.title"), t("settings.save_dir_invalid"))
            return False
        return True

    def _validate_capture_keys(self) -> bool:
        """Refuse identical combos on the two capture hotkeys. The hotkey
        manager keys its bindings by combo string, so the later registration
        would silently replace the earlier one — smart capture would stop
        working with no hint why."""
        if (
            self._smart.enabled()
            and self._fullscreen.enabled()
            and self._smart.combo() == self._fullscreen.combo()
        ):
            QMessageBox.warning(self, t("settings.title"), t("settings.capture_key_duplicate"))
            return False
        return True

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
        if not (
            self._validate_save_dir()
            and self._validate_capture_keys()
            and self._validate_editor_keys()
        ):
            return  # keep the dialog open so the user can fix the offending field
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
        self._config.set_hover_switch_delay_ms(self._hover_switch.currentData())
        self._config.set_region_adjust(self._region_adjust.isChecked())
        self._config.set_editor_backdrop(self._editor_backdrop.isChecked())
        self._config.set_autostart(self._autostart.isChecked())
        self._config.set_flash_on_capture(self._flash.isChecked())
        self._config.set_sound_on_capture(self._sound.isChecked())
        self.accept()
