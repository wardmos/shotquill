# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""The app blocklist: windows that must never reach a captured image.

A privacy feature with an honest scope. It stops ShotQuill's *own* capture
paths — GUI, CLI, and MCP — from handing a password manager (or any app the
user names) to a file, the clipboard, or an agent's model. It is **not** a
security boundary: software running as the same user can capture the screen by
other means, so this defends against an over-eager or prompt-injected agent
using ShotQuill, not a determined adversary with code execution.

The rules live in a plain JSON file (:func:`shotquill.paths.blocklist_path`)::

    {
      "version": 1,
      "rules": [
        {"bundle_id": "com.1password.1password"},
        {"name": "keychain"}
      ]
    }

A window is blocked when any rule matches it:

- ``bundle_id`` matches the window's bundle id exactly (case-insensitive) —
  the robust, default key, since bundle ids are stable and unspoofable; or
- ``name`` matches as a case-insensitive substring of the window's owner app
  name — a convenience for quick hand-edits and platforms without bundle ids.

This module is pure: it loads, saves, and matches. Enforcement (refusing a
window capture, redacting a full-screen one) lives in the capture path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from shotquill import paths
from shotquill.capture.base import WindowInfo

SCHEMA_VERSION = 1


class BlocklistError(Exception):
    """The blocklist file exists but could not be read as a valid blocklist.

    Distinct from a missing file (which is the normal "no rules" state): a
    present-but-corrupt list means the user thinks they are protected, so the
    enforcement layer surfaces this rather than silently capturing freely.
    """


@dataclass(frozen=True)
class BlockRule:
    """One blocklist entry. Exactly one of the fields is set."""

    bundle_id: str | None = None
    name: str | None = None

    def matches(self, window: WindowInfo) -> bool:
        if self.bundle_id is not None:
            return (
                window.bundle_id is not None
                and window.bundle_id.casefold() == self.bundle_id.casefold()
            )
        if self.name is not None:
            return bool(self.name) and self.name.casefold() in window.owner.casefold()
        return False

    def describe(self) -> str:
        """A short human label for warnings and the doctor report."""
        if self.bundle_id is not None:
            return self.bundle_id
        return f"name~{self.name}"

    def as_dict(self) -> dict:
        """The single-key JSON shape this rule round-trips through."""
        if self.bundle_id is not None:
            return {"bundle_id": self.bundle_id}
        return {"name": self.name}


@dataclass(frozen=True)
class Blocklist:
    """An ordered set of rules; empty means nothing is blocked."""

    rules: tuple[BlockRule, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.rules)

    def match(self, window: WindowInfo) -> BlockRule | None:
        """Return the first rule that blocks ``window``, or ``None``."""
        for rule in self.rules:
            if rule.matches(window):
                return rule
        return None

    def is_blocked(self, window: WindowInfo) -> bool:
        return self.match(window) is not None

    def blocked(self, windows: list[WindowInfo]) -> list[WindowInfo]:
        """The subset of ``windows`` any rule blocks (order preserved)."""
        return [w for w in windows if self.is_blocked(w)]


def _rule_from_dict(raw: object) -> BlockRule:
    if not isinstance(raw, dict):
        raise BlocklistError(f"each rule must be an object, got {type(raw).__name__}")
    bundle_id = raw.get("bundle_id")
    name = raw.get("name")
    if (bundle_id is None) == (name is None):
        raise BlocklistError("each rule needs exactly one of 'bundle_id' or 'name'")
    if bundle_id is not None and not isinstance(bundle_id, str):
        raise BlocklistError("'bundle_id' must be a string")
    if name is not None and not isinstance(name, str):
        raise BlocklistError("'name' must be a string")
    return BlockRule(bundle_id=bundle_id, name=name)


def load(path: Path | None = None) -> Blocklist:
    """Read the blocklist. A missing file is the empty list (the default).

    Raises :class:`BlocklistError` when the file is present but malformed, so a
    typo can be reported instead of quietly leaving the user unprotected.
    """
    path = path or paths.blocklist_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Blocklist()
    except OSError as exc:
        raise BlocklistError(f"cannot read {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BlocklistError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise BlocklistError(f"{path} must contain a JSON object")
    raw_rules = data.get("rules", [])
    if not isinstance(raw_rules, list):
        raise BlocklistError("'rules' must be a list")
    return Blocklist(tuple(_rule_from_dict(r) for r in raw_rules))


def save(blocklist: Blocklist, path: Path | None = None) -> None:
    """Write the blocklist as JSON (creating the parent directory)."""
    path = path or paths.blocklist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": SCHEMA_VERSION, "rules": [r.as_dict() for r in blocklist.rules]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
