# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""A small editor for the app blocklist (apps that are never captured).

Reads and writes the same JSON file the CLI and MCP server use, applying each
add/remove immediately — there is no separate save step, so the list the user
sees is always what protects them. On macOS an "Add running app…" picker turns
the live app list into bundle-id rules; elsewhere that button is hidden and
rules are added by typing a bundle id or app-name substring.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import (
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

from shotquill import blocklist as bl
from shotquill.i18n import t


def running_apps() -> list[tuple[str, str]]:
    """``(display name, bundle id)`` for apps the user could pick.

    macOS only; other platforms return ``[]`` and the picker hides itself.
    """
    if sys.platform != "darwin":
        return []
    return _macos_running_apps()


def _macos_running_apps() -> list[tuple[str, str]]:  # pragma: no cover - macOS only
    """Regular (Dock-visible) running apps that expose a bundle id."""
    try:
        from AppKit import NSApplicationActivationPolicyRegular, NSWorkspace

        out: list[tuple[str, str]] = []
        for app in NSWorkspace.sharedWorkspace().runningApplications():
            if app.activationPolicy() != NSApplicationActivationPolicyRegular:
                continue
            bundle = app.bundleIdentifier()
            name = app.localizedName()
            if bundle and name:
                out.append((str(name), str(bundle)))
        return sorted(set(out))
    except Exception:
        return []


class BlocklistDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("blocklist.title"))
        self._blocklist = self._load()

        layout = QVBoxLayout(self)
        hint = QLabel(t("blocklist.hint"))
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._list = QListWidget()
        layout.addWidget(self._list)

        add_row = QHBoxLayout()
        self._type = QComboBox()
        self._type.addItem(t("blocklist.type_bundle"), "bundle_id")
        self._type.addItem(t("blocklist.type_name"), "name")
        self._value = QLineEdit()
        self._value.setPlaceholderText("com.example.app")
        self._value.returnPressed.connect(self._add_typed)
        add_button = QPushButton(t("blocklist.add"))
        add_button.clicked.connect(self._add_typed)
        add_row.addWidget(self._type)
        add_row.addWidget(self._value, 1)
        add_row.addWidget(add_button)
        layout.addLayout(add_row)

        button_row = QHBoxLayout()
        self._remove_button = QPushButton(t("blocklist.remove"))
        self._remove_button.clicked.connect(self._remove_selected)
        button_row.addWidget(self._remove_button)
        self._running_button = QPushButton(t("blocklist.add_running"))
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

    def _load(self) -> bl.Blocklist:
        try:
            return bl.load()
        except bl.BlocklistError:
            QMessageBox.warning(self, t("blocklist.title"), t("blocklist.corrupt"))
            return bl.Blocklist()

    def _refresh(self) -> None:
        self._list.clear()
        for rule in self._blocklist.rules:
            self._list.addItem(rule.describe())
        self._remove_button.setEnabled(bool(self._blocklist.rules))

    def _add_rule(self, rule: bl.BlockRule) -> None:
        """Append a rule (ignoring duplicates) and persist immediately."""
        if rule in self._blocklist.rules:
            return
        self._blocklist = bl.Blocklist(self._blocklist.rules + (rule,))
        bl.save(self._blocklist)
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
        rules = list(self._blocklist.rules)
        del rules[row]
        self._blocklist = bl.Blocklist(tuple(rules))
        bl.save(self._blocklist)
        self._refresh()

    def _add_running(self) -> None:
        apps = running_apps()
        if not apps:
            return
        labels = [f"{name} ({bundle})" for name, bundle in apps]
        choice, ok = QInputDialog.getItem(self, t("blocklist.pick_running"), "", labels, 0, False)
        if ok and choice:
            self._add_rule(bl.BlockRule(bundle_id=apps[labels.index(choice)][1]))
