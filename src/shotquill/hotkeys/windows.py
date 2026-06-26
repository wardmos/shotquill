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
computed rather than hand-listed. Bindings outside the table are ignored on
Windows because they cannot be matched and swallowed safely in the low-level hook
before the event reaches the foreground app.

The live listener can only be exercised against a real Windows session; the
binding bookkeeping and match logic here are covered by driving the Win32 event
filter directly.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from queue import Queue

from pynput import keyboard

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


@dataclass
class _Binding:
    mods: frozenset[str]
    vk: int
    callback: Callable[[], None]


class WindowsHotkeyManager(HotkeyManager):
    def __init__(self) -> None:
        self._bindings: dict[str, Callable[[], None]] = {}
        self._listener: keyboard.Listener | None = None
        self._callback_worker: threading.Thread | None = None
        self._callback_queue: Queue[Callable[[], None] | None] = Queue()
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
        self._compiled = []
        with self._state_lock:
            self._active_mods.clear()
            self._pressed.clear()
            self._suppressed.clear()

    def start(self) -> None:
        """Compile the current bindings and ensure a listener is running.

        Like macOS and Linux, re-applying settings hot-swaps ``_compiled`` (an
        atomic reference swap, safe against the listener thread's reads) and
        leaves any running listener alone, so changing a hotkey never restarts
        the thread. Windows needs no permission gate and refuses no grabs, so
        there is nothing to preflight before listening.
        """
        self._compiled = [
            binding
            for c, cb in self._bindings.items()
            if (binding := self._compile(c, cb)) is not None
        ]
        if self._listener is not None:
            return  # already listening: the new bindings are live immediately
        if not self._bindings:
            return  # nothing to listen for yet
        self._ensure_callback_worker()
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
        if self._callback_worker is not None:
            self._callback_queue.put(None)
            self._callback_worker.join(timeout=1)
            self._callback_worker = None
            self._callback_queue = Queue()
        # Drop held-key state so a missed release can't wedge later matches.
        with self._state_lock:
            self._active_mods.clear()
            self._pressed.clear()
            self._suppressed.clear()

    @staticmethod
    def _compile(combo: str, callback: Callable[[], None]) -> _Binding | None:
        parsed = parse_combo(combo)
        mods = frozenset(m for m in ("cmd", "ctrl", "alt", "shift") if parsed[m])
        key = str(parsed["key"]).lower()
        vk = _WIN_VK.get(key)
        if vk is None:
            return None
        return _Binding(mods=mods, vk=vk, callback=callback)

    def _on_press(self, key: object) -> None:
        # Win32 hook filtering is the only live source of key state. Dispatching
        # here would happen after the event has already reached the foreground app.
        return None

    def _on_release(self, key: object) -> None:
        return None

    def _ensure_callback_worker(self) -> None:
        if self._callback_worker is not None:
            return
        self._callback_worker = threading.Thread(target=self._run_callbacks, daemon=True)
        self._callback_worker.start()

    def _run_callbacks(self) -> None:
        while True:
            callback = self._callback_queue.get()
            if callback is None:
                return
            callback()

    def _enqueue_callback(self, callback: Callable[[], None]) -> None:
        self._ensure_callback_worker()
        self._callback_queue.put(callback)

    def _match_binding(self, vk: int, active: frozenset[str]) -> _Binding | None:
        for binding in self._compiled:
            if binding.mods == active and binding.vk == vk:
                return binding
        return None

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
                active = frozenset(self._active_mods)
                if vk in self._suppressed:
                    suppress_repeat = True
                    binding = None
                elif vk in self._pressed:
                    return True
                else:
                    suppress_repeat = False
                    self._pressed.add(vk)
                    binding = self._match_binding(vk, active)
            if suppress_repeat:
                return not self._suppress_current_event()
            if binding is not None:
                if not self._suppress_current_event():
                    with self._state_lock:
                        self._pressed.discard(vk)
                    return True
                with self._state_lock:
                    self._suppressed.add(vk)
                self._enqueue_callback(binding.callback)
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
                return not self._suppress_current_event()
        return True

    def _suppress_current_event(self) -> bool:
        listener = self._listener
        if listener is not None and hasattr(listener, "suppress_event"):
            listener.suppress_event()
            return True
        return False
