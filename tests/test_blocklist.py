# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""App blocklist: storage round-trips, tolerant loading, and pure matching."""

from __future__ import annotations

import json

import pytest

from shotquill import blocklist as bl
from shotquill import paths
from shotquill.capture.base import Rect, WindowInfo


def _window(owner="Safari", bundle_id=None):
    return WindowInfo(
        window_id=1, owner=owner, title="", bounds=Rect(0, 0, 10, 10), bundle_id=bundle_id
    )


# --- matching ---------------------------------------------------------------


def test_bundle_id_matches_case_insensitively():
    rule = bl.BlockRule(bundle_id="com.1Password.1Password")
    assert rule.matches(_window(bundle_id="com.1password.1password"))


def test_bundle_id_rule_ignores_windows_without_one():
    # A name-only window (Linux backend, or an app with no bundle id) must not
    # accidentally match a bundle_id rule.
    assert not bl.BlockRule(bundle_id="com.x").matches(_window(bundle_id=None))


def test_name_matches_owner_substring():
    rule = bl.BlockRule(name="pass")
    assert rule.matches(_window(owner="1Password"))
    assert not rule.matches(_window(owner="Safari"))


def test_empty_rule_matches_nothing():
    # A rule with neither key set (constructed in code, never via load) is inert
    # rather than a wildcard — it must not block an arbitrary window.
    assert not bl.BlockRule().matches(_window(owner="1Password", bundle_id="com.x"))


def test_blocklist_match_returns_first_rule_and_blocked_filters():
    blocklist = bl.Blocklist(
        (bl.BlockRule(name="notes"), bl.BlockRule(bundle_id="com.apple.keychain"))
    )
    keychain = _window(owner="Keychain Access", bundle_id="com.apple.keychain")
    safari = _window(owner="Safari", bundle_id="com.apple.safari")
    assert blocklist.match(keychain).bundle_id == "com.apple.keychain"
    assert blocklist.match(safari) is None
    assert blocklist.blocked([safari, keychain]) == [keychain]


def test_empty_blocklist_blocks_nothing_and_is_falsy():
    blocklist = bl.Blocklist()
    assert not blocklist
    assert not blocklist.is_blocked(_window(owner="1Password", bundle_id="com.1password.1password"))


def test_describe():
    assert bl.BlockRule(bundle_id="com.x").describe() == "com.x"
    assert bl.BlockRule(name="vault").describe() == "name~vault"


# --- storage ----------------------------------------------------------------


def test_missing_file_loads_empty(tmp_path):
    assert bl.load(tmp_path / "nope.json").rules == ()


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "blocklist.json"
    original = bl.Blocklist(
        (bl.BlockRule(bundle_id="com.1password.1password"), bl.BlockRule(name="keychain"))
    )
    bl.save(original, path)
    assert bl.load(path) == original
    # And the on-disk shape is the documented schema.
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {
        "version": 1,
        "rules": [{"bundle_id": "com.1password.1password"}, {"name": "keychain"}],
    }


def test_save_creates_parent_dir(tmp_path):
    path = tmp_path / "deep" / "blocklist.json"
    bl.save(bl.Blocklist((bl.BlockRule(name="x"),)), path)
    assert path.exists()


def test_blocklist_path_under_config_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    assert paths.blocklist_path() == tmp_path / "blocklist.json"


# --- tolerant of bad input --------------------------------------------------


def test_corrupt_json_raises(tmp_path):
    path = tmp_path / "blocklist.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(bl.BlocklistError):
        bl.load(path)


def test_non_object_top_level_raises(tmp_path):
    path = tmp_path / "blocklist.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(bl.BlocklistError):
        bl.load(path)


def test_rules_must_be_a_list(tmp_path):
    path = tmp_path / "blocklist.json"
    path.write_text(json.dumps({"rules": "com.x"}), encoding="utf-8")
    with pytest.raises(bl.BlocklistError):
        bl.load(path)


def test_each_rule_must_be_an_object(tmp_path):
    path = tmp_path / "blocklist.json"
    path.write_text(json.dumps({"rules": ["com.x"]}), encoding="utf-8")
    with pytest.raises(bl.BlocklistError):
        bl.load(path)


def test_unreadable_file_raises_not_treated_as_empty(tmp_path):
    # A path that exists but can't be read as a file (here, a directory) is an
    # error, not the missing-file "no rules" state — the user isn't told they're
    # silently unprotected.
    path = tmp_path / "blocklist.json"
    path.mkdir()
    with pytest.raises(bl.BlocklistError):
        bl.load(path)


def test_rule_needs_exactly_one_key(tmp_path):
    path = tmp_path / "blocklist.json"
    for bad in ([{}], [{"bundle_id": "a", "name": "b"}]):
        path.write_text(json.dumps({"rules": bad}), encoding="utf-8")
        with pytest.raises(bl.BlocklistError):
            bl.load(path)


def test_rule_field_must_be_string(tmp_path):
    path = tmp_path / "blocklist.json"
    for bad in ([{"bundle_id": 42}], [{"name": 42}]):
        path.write_text(json.dumps({"rules": bad}), encoding="utf-8")
        with pytest.raises(bl.BlocklistError):
            bl.load(path)


def test_empty_rule_value_raises(tmp_path):
    # An empty/whitespace value would match nothing, silently leaving the user
    # unprotected against an app they think they blocked — reject it on load.
    path = tmp_path / "blocklist.json"
    for bad in ([{"name": ""}], [{"name": "   "}], [{"bundle_id": ""}]):
        path.write_text(json.dumps({"rules": bad}), encoding="utf-8")
        with pytest.raises(bl.BlocklistError):
            bl.load(path)
