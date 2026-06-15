# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""The allowlist data model: load, save, match, and the enabled flag.

The rule shape is shared with the blocklist (see test_blocklist.py); here we
cover what is allowlist-specific — the ``enabled`` flag, round-tripping it, and
that matching answers "is this allowed" rather than "is this blocked".
"""

from __future__ import annotations

import json

import pytest

from shotquill import allowlist as al
from shotquill import blocklist as bl
from shotquill.capture.base import Rect, WindowInfo

SAFARI = WindowInfo(11, "Safari", "GitHub", Rect(0, 0, 80, 60), bundle_id="com.apple.safari")
TERMINAL = WindowInfo(12, "Terminal", "zsh", Rect(0, 0, 80, 60), bundle_id="com.apple.Terminal")


def test_missing_file_is_disabled_empty(tmp_path):
    loaded = al.load(tmp_path / "nope.json")
    assert loaded.enabled is False
    assert loaded.rules == ()
    assert not loaded  # bool() is False when disabled


def test_enabled_with_rules_is_truthy_and_matches():
    allowlist = al.Allowlist(enabled=True, rules=(bl.BlockRule(bundle_id="com.apple.Terminal"),))
    assert allowlist  # enforcing
    assert allowlist.is_allowed(TERMINAL)
    assert not allowlist.is_allowed(SAFARI)
    assert allowlist.allowed([SAFARI, TERMINAL]) == [TERMINAL]


def test_enabled_with_no_rules_is_truthy_but_allows_nothing():
    # A full lockdown: enforcing, yet nothing matches.
    allowlist = al.Allowlist(enabled=True, rules=())
    assert allowlist
    assert not allowlist.is_allowed(TERMINAL)


def test_disabled_with_rules_is_falsy():
    allowlist = al.Allowlist(enabled=False, rules=(bl.BlockRule(name="terminal"),))
    assert not allowlist  # not enforcing, even though it has rules


def test_save_then_load_round_trips_enabled_and_rules(tmp_path):
    path = tmp_path / "allowlist.json"
    original = al.Allowlist(
        enabled=True,
        rules=(bl.BlockRule(bundle_id="com.apple.Terminal"), bl.BlockRule(name="firefox")),
    )
    al.save(original, path)
    assert al.load(path) == original
    # The on-disk shape carries the version and the flag.
    data = json.loads(path.read_text())
    assert data["enabled"] is True
    assert data["version"] == al.SCHEMA_VERSION
    assert data["rules"] == [{"bundle_id": "com.apple.Terminal"}, {"name": "firefox"}]


def test_load_rejects_non_boolean_enabled(tmp_path):
    path = tmp_path / "allowlist.json"
    path.write_text(json.dumps({"enabled": "yes", "rules": []}))
    with pytest.raises(al.AllowlistError):
        al.load(path)


def test_load_rejects_malformed_rule_as_allowlist_error(tmp_path):
    # A rule with neither key is malformed; the shared parser raises, and load
    # re-wraps it as AllowlistError so callers catch one error type.
    path = tmp_path / "allowlist.json"
    path.write_text(json.dumps({"enabled": True, "rules": [{}]}))
    with pytest.raises(al.AllowlistError):
        al.load(path)


def test_load_rejects_invalid_json(tmp_path):
    path = tmp_path / "allowlist.json"
    path.write_text("{not json")
    with pytest.raises(al.AllowlistError):
        al.load(path)


def test_enabled_defaults_to_false_when_absent(tmp_path):
    path = tmp_path / "allowlist.json"
    path.write_text(json.dumps({"rules": [{"name": "terminal"}]}))
    loaded = al.load(path)
    assert loaded.enabled is False
    assert len(loaded.rules) == 1
