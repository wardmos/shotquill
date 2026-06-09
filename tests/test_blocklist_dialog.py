# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""The Settings blocklist editor: live add/remove against the real JSON file."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from shotquill import blocklist as bl  # noqa: E402


@pytest.fixture
def dialog(qapp, monkeypatch):
    from shotquill.ui import blocklist_dialog as mod

    def _make():
        return mod.BlocklistDialog()

    return _make, mod


def _rules_on_disk():
    return bl.load().rules


def test_loads_existing_rules(dialog):
    make, _ = dialog
    bl.save(bl.Blocklist((bl.BlockRule(bundle_id="com.x"), bl.BlockRule(name="keychain"))))
    dlg = make()
    assert dlg._list.count() == 2
    assert dlg._list.item(0).text() == "com.x"
    assert dlg._list.item(1).text() == "name~keychain"


def test_add_bundle_id_persists(dialog):
    make, _ = dialog
    dlg = make()
    dlg._type.setCurrentIndex(0)  # Bundle ID
    dlg._value.setText("com.1password.1password")
    dlg._add_typed()
    assert _rules_on_disk() == (bl.BlockRule(bundle_id="com.1password.1password"),)
    assert dlg._list.count() == 1
    assert dlg._value.text() == ""  # cleared after add


def test_add_name_persists(dialog):
    make, _ = dialog
    dlg = make()
    dlg._type.setCurrentIndex(1)  # App name
    dlg._value.setText("keychain")
    dlg._add_typed()
    assert _rules_on_disk() == (bl.BlockRule(name="keychain"),)


def test_add_is_deduplicated(dialog):
    make, _ = dialog
    dlg = make()
    dlg._value.setText("com.x")
    dlg._add_typed()
    dlg._value.setText("com.x")
    dlg._add_typed()
    assert len(_rules_on_disk()) == 1


def test_blank_value_is_ignored(dialog):
    make, _ = dialog
    dlg = make()
    dlg._value.setText("   ")
    dlg._add_typed()
    assert _rules_on_disk() == ()


def test_remove_selected_persists(dialog):
    make, _ = dialog
    bl.save(bl.Blocklist((bl.BlockRule(bundle_id="com.x"), bl.BlockRule(name="keychain"))))
    dlg = make()
    dlg._list.setCurrentRow(0)
    dlg._remove_selected()
    assert _rules_on_disk() == (bl.BlockRule(name="keychain"),)


def test_remove_with_no_selection_is_noop(dialog):
    make, _ = dialog
    bl.save(bl.Blocklist((bl.BlockRule(name="x"),)))
    dlg = make()
    dlg._list.setCurrentRow(-1)
    dlg._remove_selected()
    assert len(_rules_on_disk()) == 1


def test_running_button_hidden_without_running_apps(dialog, monkeypatch):
    make, mod = dialog
    # No running apps to pick (always true off macOS; forced here so the test
    # is deterministic on a macOS runner that does have some) → picker hides.
    monkeypatch.setattr(mod, "running_apps", lambda: [])
    dlg = make()
    assert dlg._running_button.isVisibleTo(dlg) is False


def test_running_button_shown_with_running_apps(dialog, monkeypatch):
    make, mod = dialog
    monkeypatch.setattr(mod, "running_apps", lambda: [("Safari", "com.apple.safari")])
    dlg = make()
    assert dlg._running_button.isVisibleTo(dlg) is True


def test_add_running_app(dialog, monkeypatch):
    make, mod = dialog
    monkeypatch.setattr(mod, "running_apps", lambda: [("Safari", "com.apple.safari")])
    from PySide6.QtWidgets import QInputDialog

    monkeypatch.setattr(
        QInputDialog, "getItem", lambda *a, **k: ("Safari (com.apple.safari)", True)
    )
    dlg = make()
    dlg._add_running()
    assert _rules_on_disk() == (bl.BlockRule(bundle_id="com.apple.safari"),)


def test_corrupt_file_warns_and_starts_empty(dialog, monkeypatch):
    make, mod = dialog
    from shotquill import paths

    paths.blocklist_path().write_text("{bad json", encoding="utf-8")
    warned = []
    monkeypatch.setattr(mod.QMessageBox, "warning", lambda *a, **k: warned.append(a))
    dlg = make()
    assert warned  # the user is told the file couldn't be read
    assert dlg._list.count() == 0
