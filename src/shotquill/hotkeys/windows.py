# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Windows global hotkeys via ``pynput``.

pynput's Win32 backend listens for key events without any special permission
(unlike macOS Input Monitoring) and without the compositor restriction Wayland
imposes, so the manager is the simplest of the three: spin up a listener and
match. Events arrive on pynput's listener thread, so callbacks must not touch Qt
directly — the app layer marshals them onto the main thread with a queued
signal, the same as on macOS and Linux.

Like macOS — and unlike X11 — we match the final key by its hardware *virtual-key
code* (``vk``) rather than its produced character. On Windows, holding Ctrl makes
``ToUnicode`` emit a control character (Ctrl+A → ``"\\x01"``), so ``KeyCode.char``
is unreliable for the common ``Ctrl+<letter>`` combos; the VK code is positional
and constant regardless of the modifiers held. Windows VK codes are themselves
trivial — letters and digits *are* their uppercase ASCII code points
(``VK_A == 0x41``), and ``VK_F1..VK_F12`` run ``0x70..0x7B`` — so the table is
computed rather than hand-listed. Keys outside the table fall back to ``char``.

The live listener can only be exercised against a real Windows session; the
binding bookkeeping and match logic here are covered by driving
``on_press``/``on_release`` with synthetic keys, mirroring the macOS/Linux tests.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

from pynput import keyboard
from pynput.keyboard import Key

from shotquill.hotkeys.base import HotkeyManager
from shotquill.hotkeys.combo import parse_combo

# Windows virtual-key codes, keyed by the lowercase token the settings UI emits.
# Letters/digits map to their uppercase ASCII code point (the Win32 convention),
# and the function keys occupy the contiguous ``VK_F1..VK_F12`` range.
_WIN_VK: dict[str, int] = {chr(c).lower(): c for c in range(ord("A"), ord("Z") + 1)}
_WIN_VK.update({chr(c): c for c in range(ord("0"), ord("9") + 1)})
_WIN_VK.update({f"f{n}": 0x70 + (n - 1) for n in range(1, 13)})
_WIN_VK.update(
    {
        ";": 0xBA,
        "=": 0xBB,
        ",": 0xBC,
        "-": 0xBD,
        ".": 0xBE,
        "/": 0xBF,
        "`": 0xC0,
        "[": 0xDB,
        "\\": 0xDC,
        "]": 0xDD,
        "'": 0xDE,
    }
)

# Every pynput modifier key (generic + left/right variants) -> canonical name.
# Key.cmd is the Windows ("Super"/Win) key.
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
if hasattr(Key, "alt_gr"):  # AltGr on some layouts; treat as Alt
    _MOD_NAMES[Key.alt_gr] = "alt"

_WIN_MOD_VK: dict[int, str] = {
    0x10: "shift",
    0xA0: "shift",
    0xA1: "shift",
    0x11: "ctrl",
    0xA2: "ctrl",
    0xA3: "ctrl",
    0x12: "alt",
    0xA4: "alt",
    0xA5: "alt",
    0x5B: "cmd",
    0x5C: "cmd",
}


def _vk_of(key: object) -> int | None:
    """Hardware virtual-key code for a pynput key (``KeyCode`` or ``Key`` member)."""
    vk = getattr(key, "vk", None)
    if vk is None:
        value = getattr(key, "value", None)  # Key enum members wrap a KeyCode
        vk = getattr(value, "vk", None)
    return vk


@dataclass
class _Binding:
    mods: frozenset[str]
    vk: int | None  # expected virtual-key code, when the key is known
    char: str  # fallback target for keys outside the vk table
    callback: Callable[[], None]


class WindowsHotkeyManager(HotkeyManager):
    def __init__(self) -> None:
        self._bindings: dict[str, Callable[[], None]] = {}
        self._listener: keyboard.Listener | None = None
        self._compiled: list[_Binding] = []
        self._active_mods: set[str] = set()
        self._pressed: set[object] = set()  # non-modifier keys currently held
        self._suppressed: set[object] = set()  # keys whose matching key-up must also be swallowed
        # ``_active_mods``/``_pressed`` are mutated on pynput's listener thread
        # and cleared from the main thread in ``stop()``; this serialises both.
        self._state_lock = threading.Lock()

    def register(
        self, combo: str, callback: Callable[[], None], description: str | None = None
    ) -> None:
        # ``description`` is only meaningful to the Wayland portal backend; this
        # raw pynput listener grabs by key, so it is accepted and ignored here.
        self._bindings[combo] = callback

    def unregister(self, combo: str) -> None:
        self._bindings.pop(combo, None)

    def clear(self) -> None:
        self._bindings.clear()

    def start(self) -> None:
        """Compile the current bindings and ensure a listener is running.

        Like macOS and Linux, re-applying settings hot-swaps ``_compiled`` (an
        atomic reference swap, safe against the listener thread's reads) and
        leaves any running listener alone, so changing a hotkey never restarts
        the thread. Windows needs no permission gate and refuses no grabs, so
        there is nothing to preflight before listening.
        """
        self._compiled = [self._compile(c, cb) for c, cb in self._bindings.items()]
        if self._listener is not None:
            return  # already listening: the new bindings are live immediately
        if not self._bindings:
            return  # nothing to listen for yet
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
            win32_event_filter=self._event_filter,
        )
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        # Drop held-key state so a missed release can't wedge later matches.
        with self._state_lock:
            self._active_mods.clear()
            self._pressed.clear()
            self._suppressed.clear()

    @staticmethod
    def _compile(combo: str, callback: Callable[[], None]) -> _Binding:
        parsed = parse_combo(combo)
        mods = frozenset(m for m in ("cmd", "ctrl", "alt", "shift") if parsed[m])
        key = str(parsed["key"]).lower()
        return _Binding(mods=mods, vk=_WIN_VK.get(key), char=key, callback=callback)

    def _on_press(self, key: object) -> None:
        name = _MOD_NAMES.get(key)
        if name is not None:
            with self._state_lock:
                self._active_mods.add(name)
            return
        vk = _vk_of(key)
        char = getattr(key, "char", None)
        ident = vk if vk is not None else char
        with self._state_lock:
            if ident in self._pressed:  # ignore auto-repeat while the key is held
                return
            self._pressed.add(ident)
        # Live Win32 suppression happens before pynput translates the event to a
        # character, so only VK-backed bindings can be consumed reliably. Unknown
        # char-only bindings must not fire here after the event has already passed
        # through to the foreground app.
        if vk is not None:
            self._dispatch(vk, char)

    def _on_release(self, key: object) -> None:
        name = _MOD_NAMES.get(key)
        if name is not None:
            with self._state_lock:
                self._active_mods.discard(name)
            return
        vk = _vk_of(key)
        char = getattr(key, "char", None)
        with self._state_lock:
            self._pressed.discard(vk if vk is not None else char)

    def _dispatch(self, vk: int | None, char: object) -> bool:
        char_l = char.lower() if isinstance(char, str) else None
        with self._state_lock:
            active = frozenset(self._active_mods)
        for binding in self._compiled:
            if binding.mods != active:
                continue
            if binding.vk is not None:
                if vk == binding.vk:
                    binding.callback()
                    return True
            elif char_l is not None and char_l == binding.char:
                binding.callback()
                return True
        return False

    def _event_filter(self, msg, data) -> bool:
        press_messages = {0x0100, 0x0104}  # WM_KEYDOWN, WM_SYSKEYDOWN
        release_messages = {0x0101, 0x0105}  # WM_KEYUP, WM_SYSKEYUP
        vk = getattr(data, "vkCode", None)
        if vk is None:
            return True
        mod = _WIN_MOD_VK.get(vk)
        if msg in press_messages:
            if mod is not None:
                with self._state_lock:
                    self._active_mods.add(mod)
                return True
            with self._state_lock:
                if vk in self._suppressed:
                    suppress_repeat = True
                elif vk in self._pressed:
                    return True
                else:
                    suppress_repeat = False
                    self._pressed.add(vk)
            if suppress_repeat:
                self._suppress_current_event()
                return False
            if self._dispatch(vk, None):
                with self._state_lock:
                    self._suppressed.add(vk)
                self._suppress_current_event()
                return False
            with self._state_lock:
                self._pressed.discard(vk)
        elif msg in release_messages:
            if mod is not None:
                with self._state_lock:
                    self._active_mods.discard(mod)
                return True
            with self._state_lock:
                was_suppressed = vk in self._suppressed
                if was_suppressed:
                    self._pressed.discard(vk)
                    self._suppressed.discard(vk)
            if was_suppressed:
                self._suppress_current_event()
                return False
        return True

    def _suppress_current_event(self) -> None:
        listener = self._listener
        if listener is not None and hasattr(listener, "suppress_event"):
            listener.suppress_event()
