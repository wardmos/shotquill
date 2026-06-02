# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Settings dialog: language, save directory, image format, and custom hotkeys."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from shotquill.hotkeys.combo import parse_combo, to_pynput_combo
from shotquill.i18n import LANGUAGE_NAMES, LANGUAGES, t

if TYPE_CHECKING:
    from shotquill.config import Config

_KEYS = [*"abcdefghijklmnopqrstuvwxyz", *"0123456789", *(f"f{i}" for i in range(1, 13))]
_FORMATS = ["png", "jpg"]


class _HotkeyRow(QWidget):
    """⌘/⌃/⌥/⇧ checkboxes plus a key dropdown that yield a pynput combo string."""

    def __init__(self, combo: str) -> None:
        super().__init__()
        parsed = parse_combo(combo)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

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

        for widget in (self._cmd, self._ctrl, self._alt, self._shift, self._key):
            layout.addWidget(widget)
        layout.addStretch()

    def combo(self) -> str:
        return to_pynput_combo(
            cmd=self._cmd.isChecked(),
            ctrl=self._ctrl.isChecked(),
            alt=self._alt.isChecked(),
            shift=self._shift.isChecked(),
            key=self._key.currentText(),
        )


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

        self._region = _HotkeyRow(config.hotkey("region_capture"))
        self._fullscreen = _HotkeyRow(config.hotkey("fullscreen_capture"))
        form.addRow(t("settings.region"), self._region)
        form.addRow(t("settings.fullscreen"), self._fullscreen)

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

    def _save_and_accept(self) -> None:
        self._config.set_language(self._language.currentData())
        self._config.set_save_dir(self._save_dir.text())
        self._config.set_image_format(self._format.currentText())
        self._config.set_hotkey("region_capture", self._region.combo())
        self._config.set_hotkey("fullscreen_capture", self._fullscreen.combo())
        self.accept()
