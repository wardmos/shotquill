# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""The capture allowlist: when enabled, the *only* windows that may be captured.

The complement of the blocklist. The blocklist names apps that must never be
captured; the allowlist inverts the default — when it is **enabled**, a capture
is refused unless its target app is on the list. It is the tighter leash you
hand an agent: "you may screenshot Terminal and the browser, and nothing else."

Like the blocklist it is an honest privacy control, not a security boundary
(see :mod:`shotquill.blocklist`): it constrains ShotQuill's own capture paths —
GUI, CLI, and MCP — so an over-eager or prompt-injected agent cannot wander off
its leash, not an adversary with code execution.

The rules live in a plain JSON file (:func:`shotquill.paths.allowlist_path`),
sharing the blocklist's app-matching rule shape but adding an ``enabled`` flag::

    {
      "version": 1,
      "enabled": true,
      "rules": [
        {"bundle_id": "com.apple.Terminal"},
        {"name": "firefox"}
      ]
    }

``enabled`` lives in the file (not QSettings) so the headless surface enforces
it without a running Qt application. When ``enabled`` is false (the default, and
the state of a missing file) the allowlist does nothing and capture behaves
exactly as before. An *enabled* list with no rules allows nothing — a deliberate
full lockdown, not a no-op.

This module is pure: it loads, saves, and matches. Enforcement (refusing a
capture whose target is not allowed) lives in :mod:`shotquill.headless`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from shotquill import paths
from shotquill.blocklist import BlocklistError, BlockRule, rule_from_dict
from shotquill.capture.base import WindowInfo

SCHEMA_VERSION = 1


class AllowlistError(Exception):
    """The allowlist file exists but could not be read as a valid allowlist.

    Distinct from a missing file (the normal "disabled" state): a present-but-
    corrupt list while the feature is meant to be on means the user thinks they
    have a leash that is actually broken, so enforcement fails closed (refuses to
    capture) rather than silently capturing freely.
    """


@dataclass(frozen=True)
class Allowlist:
    """An ``enabled`` flag plus an ordered set of allow rules.

    The rule type is shared with the blocklist (:class:`shotquill.blocklist.BlockRule`)
    — a window-matching rule is the same shape whichever list it lives on.

    ``bool(allowlist)`` is true when the allowlist is *enforcing* (i.e. enabled),
    regardless of how many rules it holds, so enforcement code can branch on it
    the way it branches on ``if blocklist`` — an enabled-but-empty list still
    enforces (and allows nothing).
    """

    enabled: bool = False
    rules: tuple[BlockRule, ...] = ()

    def __bool__(self) -> bool:
        return self.enabled

    def match(self, window: WindowInfo) -> BlockRule | None:
        """Return the first rule that allows ``window``, or ``None``."""
        for rule in self.rules:
            if rule.matches(window):
                return rule
        return None

    def is_allowed(self, window: WindowInfo) -> bool:
        """Whether ``window`` may be captured while the allowlist is enforcing."""
        return self.match(window) is not None

    def allowed(self, windows: list[WindowInfo]) -> list[WindowInfo]:
        """The subset of ``windows`` the allowlist permits (order preserved)."""
        return [w for w in windows if self.is_allowed(w)]


def _to_bool(value: object) -> bool:
    """Coerce the JSON ``enabled`` field, accepting only a real boolean.

    The file is hand-editable, so a non-boolean ``enabled`` is a typo we must not
    silently read as "on" (over-locked) or "off" (unprotected) — surface it."""
    if not isinstance(value, bool):
        raise AllowlistError("'enabled' must be true or false")
    return value


def load(path: Path | None = None) -> Allowlist:
    """Read the allowlist. A missing file is the disabled empty list (default).

    Raises :class:`AllowlistError` when the file is present but malformed, so a
    typo is reported instead of quietly leaving the leash in an unknown state.
    """
    path = path or paths.allowlist_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Allowlist()
    except OSError as exc:
        raise AllowlistError(f"cannot read {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AllowlistError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise AllowlistError(f"{path} must contain a JSON object")
    enabled = _to_bool(data.get("enabled", False))
    raw_rules = data.get("rules", [])
    if not isinstance(raw_rules, list):
        raise AllowlistError("'rules' must be a list")
    try:
        rules = tuple(rule_from_dict(r) for r in raw_rules)
    except BlocklistError as exc:
        # The rule shape is shared with the blocklist; re-wrap its parse error in
        # our own type so callers (e.g. headless.active_allowlist) catch one kind.
        raise AllowlistError(str(exc)) from exc
    return Allowlist(enabled=enabled, rules=rules)


def save(allowlist: Allowlist, path: Path | None = None) -> None:
    """Write the allowlist as JSON (creating the parent directory)."""
    path = path or paths.allowlist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": SCHEMA_VERSION,
        "enabled": allowlist.enabled,
        "rules": [r.as_dict() for r in allowlist.rules],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
