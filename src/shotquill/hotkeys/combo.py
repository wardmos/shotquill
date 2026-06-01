# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Build and parse pynput hotkey combo strings (pure, unit-testable).

The settings UI exposes explicit ⌘/⌃/⌥/⇧ checkboxes that map directly to the
physical keys pynput listens for, so there is no macOS Ctrl/Cmd-swap ambiguity.
"""

from __future__ import annotations

_MODIFIERS = ("cmd", "ctrl", "alt", "shift")


def to_pynput_combo(
    *,
    cmd: bool = False,
    ctrl: bool = False,
    alt: bool = False,
    shift: bool = False,
    key: str,
) -> str:
    """Assemble a combo like ``<cmd>+<shift>+a`` from modifier flags and a key."""
    if not key:
        raise ValueError("a hotkey needs a non-modifier key")
    parts = []
    if cmd:
        parts.append("<cmd>")
    if ctrl:
        parts.append("<ctrl>")
    if alt:
        parts.append("<alt>")
    if shift:
        parts.append("<shift>")
    parts.append(key.lower())
    return "+".join(parts)


def parse_combo(combo: str) -> dict[str, bool | str]:
    """Inverse of :func:`to_pynput_combo`: ``<alt>+a`` -> flags + ``key``."""
    tokens = [t.strip().lower() for t in combo.split("+") if t.strip()]
    result: dict[str, bool | str] = {m: f"<{m}>" in tokens for m in _MODIFIERS}
    keys = [t for t in tokens if not (t.startswith("<") and t.endswith(">"))]
    result["key"] = keys[-1] if keys else ""
    return result
