# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Linux (X11) global hotkeys via ``pynput``.

pynput's X11 backend listens for key events without a special permission (unlike
macOS Input Monitoring). Events arrive on pynput's listener thread, so callbacks
must not touch Qt directly — the app layer marshals them onto the main thread
with a queued signal, same as on macOS.

Unlike macOS — where the Option modifier rewrites the produced character, forcing
hardware key-code matching — X11 reports the ordinary character for letter/digit
keys, so we match the final key by its lowercased ``char`` (and function keys by
their ``Key`` member). The live listener can only be exercised against a real X
server; the binding bookkeeping and match logic here are covered by driving
``on_press``/``on_release`` with synthetic keys.

This is the *X11* slice. Wayland blocks global key grabs by design and is served
by :class:`shotquill.hotkeys.wayland.WaylandHotkeyManager` (the GlobalShortcuts
portal), which the :func:`shotquill.hotkeys.get_manager` factory selects on a
Wayland session. The Wayland guard below stays as defense-in-depth for anyone
constructing this X11 manager directly there: ``start`` detects the session and
raises :class:`HotkeyUnavailable` rather than spinning up a listener that would
never see events — silent failure is the worst outcome here, since the user has
visibly configured hotkeys in Settings.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass

from pynput import keyboard
from pynput.keyboard import Key

from shotquill.hotkeys.base import HotkeyManager, HotkeyUnavailable
from shotquill.hotkeys.combo import parse_combo


def _is_wayland_session() -> bool:
    """True on a real Wayland desktop, where global key grabs are refused.

    Mirrors :func:`shotquill.headless._is_wayland_session`: an explicit
    ``QT_QPA_PLATFORM`` (e.g. tests forcing ``offscreen``) wins so the manager
    stays exercisable without a live X server."""
    if os.environ.get("QT_QPA_PLATFORM"):
        return False
    return os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(
        os.environ.get("WAYLAND_DISPLAY")
    )


# Every pynput modifier key (generic + left/right variants) -> canonical name.
# Key.cmd is the Super/Meta key on Linux.
_MOD_NAMES: dict[object, str] = {
    Key.alt: "alt",
    Key.alt_l: "alt",
    Key.alt_r: "alt",
    Key.cmd: "cmd",
    Key.cmd_l: "cmd",
    Key.cmd_r: "cmd",
    Key.ctrl: "ctrl",
    Key.ctrl_l: "ctrl",
    Key.ctrl_r: "ctrl",
    Key.shift: "shift",
    Key.shift_l: "shift",
    Key.shift_r: "shift",
}
if hasattr(Key, "alt_gr"):
    _MOD_NAMES[Key.alt_gr] = "alt"

# Function keys arrive as Key members (no .char), so map them to the f1–f12
# tokens the settings UI emits.
_FUNCTION_KEYS: dict[object, str] = {
    getattr(Key, f"f{n}"): f"f{n}" for n in range(1, 13) if hasattr(Key, f"f{n}")
}


def _key_token(key: object) -> str | None:
    """The combo token for a pressed key: ``f1``–``f12`` for function keys, the
    lowercased character otherwise, or ``None`` for anything unmatchable."""
    if key in _FUNCTION_KEYS:
        return _FUNCTION_KEYS[key]
    char = getattr(key, "char", None)
    return char.lower() if isinstance(char, str) else None


@dataclass
class _Binding:
    mods: frozenset[str]
    key: str  # the lowercased token (a–z, 0–9, f1–f12)
    callback: Callable[[], None]


class LinuxHotkeyManager(HotkeyManager):
    def __init__(self) -> None:
        self._bindings: dict[str, Callable[[], None]] = {}
        self._listener: keyboard.Listener | None = None
        self._compiled: list[_Binding] = []
        self._active_mods: set[str] = set()
        self._pressed: set[str] = set()  # non-modifier key tokens currently held
        # ``_active_mods``/``_pressed`` are mutated on pynput's listener thread
        # and cleared from the main thread in ``stop()``; this serialises both.
        self._state_lock = threading.Lock()

    def register(
        self, combo: str, callback: Callable[[], None], description: str | None = None
    ) -> None:
        # ``description`` is only meaningful to the Wayland portal backend; this
        # raw pynput X11 listener grabs by key, so it is accepted and ignored here.
        self._bindings[combo] = callback

    def unregister(self, combo: str) -> None:
        self._bindings.pop(combo, None)

    def clear(self) -> None:
        self._bindings.clear()

    def start(self) -> None:
        """Compile the current bindings and ensure a listener is running.

        Like macOS, re-applying settings hot-swaps ``_compiled`` (an atomic
        reference swap, safe against the listener thread's reads) and leaves any
        running listener alone, so changing a hotkey never restarts the thread.

        On a Wayland session :class:`HotkeyUnavailable` is raised before spinning
        up a listener: pynput's X11 backend would start cleanly but never receive
        events under a Wayland compositor, leaving the user with a silently dead
        hotkey. Failing loudly here lets the app fall back to the menu/tray.
        """
        if self._bindings and _is_wayland_session():
            raise HotkeyUnavailable(
                "Wayland blocks global key grabs; use the tray menu or bind a "
                "compositor shortcut to `squill capture`."
            )
        self._compiled = [self._compile(c, cb) for c, cb in self._bindings.items()]
        if self._listener is not None:
            return  # already listening: the new bindings are live immediately
        if not self._bindings:
            return  # nothing to listen for yet
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        # Drop held-key state so a missed release can't wedge later matches.
        with self._state_lock:
            self._active_mods.clear()
            self._pressed.clear()

    @staticmethod
    def _compile(combo: str, callback: Callable[[], None]) -> _Binding:
        parsed = parse_combo(combo)
        mods = frozenset(m for m in ("cmd", "ctrl", "alt", "shift") if parsed[m])
        return _Binding(mods=mods, key=str(parsed["key"]).lower(), callback=callback)

    def _on_press(self, key: object) -> None:
        name = _MOD_NAMES.get(key)
        if name is not None:
            with self._state_lock:
                self._active_mods.add(name)
            return
        token = _key_token(key)
        if token is None:
            return
        with self._state_lock:
            if token in self._pressed:  # ignore auto-repeat while held
                return
            self._pressed.add(token)
        self._dispatch(token)

    def _on_release(self, key: object) -> None:
        name = _MOD_NAMES.get(key)
        if name is not None:
            with self._state_lock:
                self._active_mods.discard(name)
            return
        token = _key_token(key)
        if token is not None:
            with self._state_lock:
                self._pressed.discard(token)

    def _dispatch(self, token: str) -> None:
        with self._state_lock:
            active = frozenset(self._active_mods)
        for binding in self._compiled:
            if binding.mods == active and binding.key == token:
                binding.callback()
