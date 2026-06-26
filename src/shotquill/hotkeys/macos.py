# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""macOS global hotkeys via ``pynput``.

pynput delivers key events on its own listener thread, so callbacks must not
touch Qt UI directly — the app layer marshals them onto the main thread with a
queued signal. Requires the "Input Monitoring" permission on macOS.

We do **not** use ``pynput.keyboard.GlobalHotKeys`` because it matches the final
key by *character*. On macOS the Option (⌥) modifier is the "alternate
character" key: ⌥A emits "å", ⌥S "ß", ⌥W "∑", so character matching makes
Option-based combos fire only intermittently. Instead we run a raw listener and
match the final key by its hardware *key code* (``vk``), which is independent of
the modifiers held — exactly how reliable Option-friendly hotkey apps behave.

The matched key is also **swallowed** so it does not leak to the foreground app:
``darwin_intercept`` returns ``None`` for the Quartz event that just fired a
binding, consuming it system-wide. The modifying event tap this needs is covered
by the same Input Monitoring grant the listener already requires — no separate
Accessibility permission, unlike ``CGEventTap``-style suppression is sometimes
mistaken to need.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

from pynput import keyboard
from pynput.keyboard import Key

from shotquill.hotkeys.base import HotkeyManager
from shotquill.hotkeys.combo import parse_combo
from shotquill.permissions import quartz_function

_INPUT_MONITORING_ERROR = "Input Monitoring permission is required for global hotkeys."

# macOS hardware virtual key codes (kVK_ANSI_* / kVK_F*), keyed by the lowercase
# token the settings UI emits (a–z, 0–9, f1–f12). The key code is positional and
# does not change when Option rewrites the produced character, so matching on it
# is what makes Option combos reliable.
_MAC_VK: dict[str, int] = {
    # letters
    "a": 0,
    "s": 1,
    "d": 2,
    "f": 3,
    "h": 4,
    "g": 5,
    "z": 6,
    "x": 7,
    "c": 8,
    "v": 9,
    "b": 11,
    "q": 12,
    "w": 13,
    "e": 14,
    "r": 15,
    "y": 16,
    "t": 17,
    "o": 31,
    "u": 32,
    "i": 34,
    "p": 35,
    "l": 37,
    "j": 38,
    "k": 40,
    "n": 45,
    "m": 46,
    # number row
    "1": 18,
    "2": 19,
    "3": 20,
    "4": 21,
    "5": 23,
    "6": 22,
    "7": 26,
    "8": 28,
    "9": 25,
    "0": 29,
    # function keys
    "f1": 122,
    "f2": 120,
    "f3": 99,
    "f4": 118,
    "f5": 96,
    "f6": 97,
    "f7": 98,
    "f8": 100,
    "f9": 101,
    "f10": 109,
    "f11": 103,
    "f12": 111,
}

# Every pynput modifier key (generic + left/right variants) -> canonical name.
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
if hasattr(Key, "alt_gr"):  # present on some layouts; treat as Option
    _MOD_NAMES[Key.alt_gr] = "alt"


def _vk_of(key: object) -> int | None:
    """Hardware key code for a pynput key (a ``KeyCode`` or a ``Key`` member)."""
    vk = getattr(key, "vk", None)
    if vk is None:
        value = getattr(key, "value", None)  # Key enum members wrap a KeyCode
        vk = getattr(value, "vk", None)
    return vk


def has_input_monitoring_access() -> bool:
    """Whether macOS currently allows this process to listen for key events.

    Fails *open* (True) when the state can't be read — the listener gate must
    not block hotkeys on Linux development or an old macOS, unlike the
    settings UI, which shows the same situation as "unknown".
    """
    preflight = quartz_function("CGPreflightListenEventAccess")
    if preflight is None:
        return True
    try:
        return bool(preflight())
    except Exception:
        return True


def request_input_monitoring_access() -> bool:
    """Ask macOS for Input Monitoring access if needed; return whether it is granted."""
    if has_input_monitoring_access():
        return True
    request = quartz_function("CGRequestListenEventAccess")
    if request is None:
        return True
    try:
        return bool(request())
    except Exception:
        return has_input_monitoring_access()


@dataclass
class _Binding:
    mods: frozenset[str]
    vk: int | None  # expected hardware key code, when the key is known
    char: str  # fallback target for keys outside the vk table
    callback: Callable[[], None]


class MacHotkeyManager(HotkeyManager):
    def __init__(self) -> None:
        self._bindings: dict[str, Callable[[], None]] = {}
        self._listener: keyboard.Listener | None = None
        self._compiled: list[_Binding] = []
        self._active_mods: set[str] = set()
        self._pressed: set[object] = set()  # non-modifier keys currently held
        self._suppressed: set[object] = set()
        self._suppress_next_event = False
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
        self._compiled = []
        with self._state_lock:
            self._active_mods.clear()
            self._pressed.clear()
            self._suppressed.clear()
            self._suppress_next_event = False

    def start(self) -> None:
        """Compile the current bindings and make sure a listener is running.

        The listener is started at most once per process: stopping a pynput
        listener and starting a fresh one while Qt owns the main loop trips a
        dispatch assertion inside macOS frameworks (EXC_BREAKPOINT/SIGTRAP on
        the new listener thread), killing the app — observed when re-applying
        hotkeys after the Settings dialog. So re-applying settings hot-swaps
        ``_compiled`` (an atomic reference swap, safe to race with the listener
        thread's reads) and leaves the running listener untouched.
        """
        self._compiled = [self._compile(c, cb) for c, cb in self._bindings.items()]
        if self._listener is not None:
            return  # already listening: the new bindings are live immediately
        if not self._bindings:
            return  # nothing to listen for; don't prompt for permission yet
        if not request_input_monitoring_access():
            raise PermissionError(_INPUT_MONITORING_ERROR)
        # The same event tap that Input Monitoring lets us listen through can
        # also *swallow* the matched key: ``darwin_intercept`` runs after
        # ``on_press`` for each Quartz event and, by returning ``None`,
        # consumes it system-wide so it never reaches the foreground app. No
        # Accessibility grant is needed — Input Monitoring covers the
        # modifying tap — so suppression is always on, not gated behind a
        # second permission the user would have to discover.
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
            darwin_intercept=self._intercept,
        )
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        # Drop held-key state so a missed release can't wedge later matches. Keep
        # the one-event suppression flag intact: pynput calls darwin_intercept
        # after on_press for the same event, and stop() can race between them.
        with self._state_lock:
            self._active_mods.clear()
            self._pressed.clear()
            self._suppressed.clear()

    @staticmethod
    def _compile(combo: str, callback: Callable[[], None]) -> _Binding:
        parsed = parse_combo(combo)
        mods = frozenset(m for m in ("cmd", "ctrl", "alt", "shift") if parsed[m])
        key = str(parsed["key"]).lower()
        return _Binding(mods=mods, vk=_MAC_VK.get(key), char=key, callback=callback)

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
            if ident in self._suppressed:
                self._suppress_next_event = True
                return
            if ident in self._pressed:  # ignore auto-repeat while the key is held
                return
            self._pressed.add(ident)
        if self._dispatch(vk, char):
            with self._state_lock:
                self._suppressed.add(ident)
                self._suppress_next_event = True

    def _on_release(self, key: object) -> None:
        name = _MOD_NAMES.get(key)
        if name is not None:
            with self._state_lock:
                self._active_mods.discard(name)
            return
        vk = _vk_of(key)
        char = getattr(key, "char", None)
        ident = vk if vk is not None else char
        with self._state_lock:
            self._pressed.discard(ident)
            if ident in self._suppressed:
                self._suppressed.discard(ident)
                self._suppress_next_event = True

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

    def _intercept(self, event_type, event):
        """Suppress the key event that just fired a ShotQuill hotkey.

        pynput calls ``darwin_intercept`` after dispatching ``on_press`` for the
        same Quartz event. Returning ``None`` consumes that event system-wide.
        """
        with self._state_lock:
            suppress = self._suppress_next_event
            self._suppress_next_event = False
        if suppress:
            return None
        return event
