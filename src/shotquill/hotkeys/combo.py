# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Build and parse pynput hotkey combo strings (pure, unit-testable).

The settings UI exposes explicit ⌘/⌃/⌥/⇧ checkboxes that map directly to the
physical keys pynput listens for, so there is no macOS Ctrl/Cmd-swap ambiguity.
"""

from __future__ import annotations

_MODIFIERS = ("cmd", "ctrl", "alt", "shift")

# How each canonical modifier is spelled in the xdg-desktop-portal GlobalShortcuts
# ``preferred_trigger`` syntax (the "shortcuts" grammar shared with the desktop
# entry spec): plus-joined upper-case modifier names plus the key. ``cmd`` is the
# Super/Meta key on Linux, which the grammar names ``LOGO``.
_PORTAL_MODIFIERS = {"cmd": "LOGO", "ctrl": "CTRL", "alt": "ALT", "shift": "SHIFT"}


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


def to_portal_trigger(combo: str) -> str:
    """A pynput combo -> the xdg-desktop-portal GlobalShortcuts ``preferred_trigger``.

    ``<cmd>+<shift>+a`` -> ``LOGO+SHIFT+a``; ``<ctrl>+f5`` -> ``CTRL+F5``. The
    trigger is only a *hint* the compositor may honour, ignore, or let the user
    re-bind in its own settings, so a best-effort spelling is all that is needed;
    the binding the user actually triggers is matched by shortcut *id*, not by
    re-parsing this string. Function keys are upper-cased (``F5``) to match the
    grammar's key-symbol convention; ordinary character keys are passed through
    as-is.
    """
    parsed = parse_combo(combo)
    parts = [_PORTAL_MODIFIERS[m] for m in _MODIFIERS if parsed[m]]
    key = str(parsed["key"])
    if not key:
        raise ValueError("a hotkey needs a non-modifier key")
    # Function keys (f1–f12) name a key symbol, which the grammar upper-cases;
    # a plain character key stays lower-case ('a', '1').
    parts.append(key.upper() if _is_function_key(key) else key)
    return "+".join(parts)


def portal_shortcut_id(combo: str) -> str:
    """A stable, portal-safe id for a combo (the key the ``Activated`` signal
    carries back). ``<cmd>+<shift>+a`` -> ``sq_cmd_shift_a`` — deterministic, so
    the same combo always maps to the same id across rebinds, and restricted to
    ``[A-Za-z0-9_]`` so no compositor chokes on the identifier."""
    out: list[str] = []
    for c in combo:
        if c.isalnum():
            out.append(c)
        elif out and out[-1] != "_":
            out.append("_")  # collapse any run of separators (<, >, +) to one
    return "sq_" + "".join(out).strip("_")


def _is_function_key(key: str) -> bool:
    return len(key) >= 2 and key[0] == "f" and key[1:].isdigit()
