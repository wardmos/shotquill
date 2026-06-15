# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""The Settings allowlist editor: live add/remove/enable against the JSON file."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from shotquill import allowlist as al  # noqa: E402
from shotquill import blocklist as bl  # noqa: E402


@pytest.fixture
def dialog(qapp, monkeypatch):
    from shotquill.ui import allowlist_dialog as mod

    # The "enabled with no rules" warning is modal; silence it so construction
    # never blocks in a headless test.
    monkeypatch.setattr(mod.QMessageBox, "information", lambda *a, **k: None)

    def _make():
        return mod.AllowlistDialog()

    return _make, mod


def _on_disk():
    return al.load()


def test_loads_existing_state(dialog):
    make, _ = dialog
    al.save(al.Allowlist(enabled=True, rules=(bl.BlockRule(bundle_id="com.x"),)))
    dlg = make()
    assert dlg._enabled.isChecked() is True
    assert dlg._list.count() == 1
    assert dlg._list.item(0).text() == "com.x"


def test_toggle_enabled_persists(dialog):
    make, _ = dialog
    al.save(al.Allowlist(enabled=False, rules=(bl.BlockRule(name="terminal"),)))
    dlg = make()
    dlg._enabled.setChecked(True)
    loaded = _on_disk()
    assert loaded.enabled is True
    assert loaded.rules == (bl.BlockRule(name="terminal"),)  # rules untouched
    dlg._enabled.setChecked(False)
    assert _on_disk().enabled is False


def test_add_keeps_enabled_flag(dialog):
    make, _ = dialog
    al.save(al.Allowlist(enabled=True, rules=()))
    dlg = make()
    dlg._type.setCurrentIndex(0)  # Bundle ID
    dlg._value.setText("com.apple.Terminal")
    dlg._add_typed()
    loaded = _on_disk()
    assert loaded.enabled is True
    assert loaded.rules == (bl.BlockRule(bundle_id="com.apple.Terminal"),)
    assert dlg._value.text() == ""


def test_add_is_deduplicated(dialog):
    make, _ = dialog
    dlg = make()
    dlg._value.setText("com.x")
    dlg._add_typed()
    dlg._value.setText("com.x")
    dlg._add_typed()
    assert len(_on_disk().rules) == 1


def test_remove_selected_keeps_enabled_flag(dialog):
    make, _ = dialog
    al.save(
        al.Allowlist(enabled=True, rules=(bl.BlockRule(bundle_id="com.x"), bl.BlockRule(name="y")))
    )
    dlg = make()
    dlg._list.setCurrentRow(0)
    dlg._remove_selected()
    loaded = _on_disk()
    assert loaded.enabled is True
    assert loaded.rules == (bl.BlockRule(name="y"),)


def test_corrupt_file_warns_and_starts_empty(dialog, monkeypatch):
    make, mod = dialog
    from shotquill import paths

    paths.allowlist_path().write_text("{bad json", encoding="utf-8")
    warned = []
    monkeypatch.setattr(mod.QMessageBox, "warning", lambda *a, **k: warned.append(a))
    dlg = make()
    assert warned
    assert dlg._list.count() == 0
    assert dlg._enabled.isChecked() is False
