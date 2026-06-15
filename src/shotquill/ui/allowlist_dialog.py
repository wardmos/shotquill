# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""A small editor for the capture allowlist (when on, the only apps captured).

The complement of :mod:`shotquill.ui.blocklist_dialog`. Reads and writes the
same JSON file the CLI and MCP server use, applying each change immediately — the
enable toggle and every add/remove persist at once, so what the user sees is
exactly what is enforced. On macOS an "Add running app…" picker turns the live
app list into bundle-id rules; elsewhere that button is hidden and rules are
added by typing a bundle id or app-name substring.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from shotquill import allowlist as al
from shotquill import blocklist as bl
from shotquill.i18n import t
from shotquill.ui.blocklist_dialog import running_apps


class AllowlistDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("allowlist.title"))
        self._allowlist = self._load()

        layout = QVBoxLayout(self)
        hint = QLabel(t("allowlist.hint"))
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._enabled = QCheckBox(t("allowlist.enabled"))
        self._enabled.setChecked(self._allowlist.enabled)
        self._enabled.toggled.connect(self._set_enabled)
        layout.addWidget(self._enabled)

        self._list = QListWidget()
        layout.addWidget(self._list)

        add_row = QHBoxLayout()
        self._type = QComboBox()
        self._type.addItem(t("allowlist.type_bundle"), "bundle_id")
        self._type.addItem(t("allowlist.type_name"), "name")
        self._value = QLineEdit()
        self._value.setPlaceholderText("com.example.app")
        self._value.returnPressed.connect(self._add_typed)
        add_button = QPushButton(t("allowlist.add"))
        add_button.clicked.connect(self._add_typed)
        add_row.addWidget(self._type)
        add_row.addWidget(self._value, 1)
        add_row.addWidget(add_button)
        layout.addLayout(add_row)

        button_row = QHBoxLayout()
        self._remove_button = QPushButton(t("allowlist.remove"))
        self._remove_button.clicked.connect(self._remove_selected)
        button_row.addWidget(self._remove_button)
        self._running_button = QPushButton(t("allowlist.add_running"))
        self._running_button.clicked.connect(self._add_running)
        self._running_button.setVisible(bool(running_apps()))
        button_row.addWidget(self._running_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        close.accepted.connect(self.accept)
        layout.addWidget(close)

        self._refresh()

    def _load(self) -> al.Allowlist:
        try:
            return al.load()
        except al.AllowlistError:
            QMessageBox.warning(self, t("allowlist.title"), t("allowlist.corrupt"))
            return al.Allowlist()

    def _refresh(self) -> None:
        self._list.clear()
        for rule in self._allowlist.rules:
            self._list.addItem(rule.describe())
        self._remove_button.setEnabled(bool(self._allowlist.rules))
        # Enabled with no rules captures nothing — warn so the user doesn't read
        # the blanket refusals as a bug.
        if self._allowlist.enabled and not self._allowlist.rules:
            QMessageBox.information(self, t("allowlist.title"), t("allowlist.empty_enabled"))

    def _save(self) -> None:
        al.save(self._allowlist)

    def _set_enabled(self, enabled: bool) -> None:
        self._allowlist = al.Allowlist(enabled=bool(enabled), rules=self._allowlist.rules)
        self._save()
        self._refresh()

    def _add_rule(self, rule: bl.BlockRule) -> None:
        """Append a rule (ignoring duplicates) and persist immediately."""
        if rule in self._allowlist.rules:
            return
        self._allowlist = al.Allowlist(
            enabled=self._allowlist.enabled, rules=self._allowlist.rules + (rule,)
        )
        self._save()
        self._refresh()

    def _add_typed(self) -> None:
        value = self._value.text().strip()
        if not value:
            return
        if self._type.currentData() == "bundle_id":
            self._add_rule(bl.BlockRule(bundle_id=value))
        else:
            self._add_rule(bl.BlockRule(name=value))
        self._value.clear()

    def _remove_selected(self) -> None:
        row = self._list.currentRow()
        if row < 0:
            return
        rules = list(self._allowlist.rules)
        del rules[row]
        self._allowlist = al.Allowlist(enabled=self._allowlist.enabled, rules=tuple(rules))
        self._save()
        self._refresh()

    def _add_running(self) -> None:
        apps = running_apps()
        if not apps:
            return
        labels = [f"{name} ({bundle})" for name, bundle in apps]
        choice, ok = QInputDialog.getItem(self, t("allowlist.pick_running"), "", labels, 0, False)
        if ok and choice:
            self._add_rule(bl.BlockRule(bundle_id=apps[labels.index(choice)][1]))
