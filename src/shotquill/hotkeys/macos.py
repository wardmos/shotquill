# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""macOS global hotkeys via ``pynput``.

pynput delivers key events on its own listener thread, so callbacks must not
touch Qt UI directly — the app layer marshals them onto the main thread with a
queued signal. Requires the "Input Monitoring" permission on macOS.
"""

from __future__ import annotations

from collections.abc import Callable

from pynput import keyboard

from shotquill.hotkeys.base import HotkeyManager


class MacHotkeyManager(HotkeyManager):
    def __init__(self) -> None:
        self._bindings: dict[str, Callable[[], None]] = {}
        self._listener: keyboard.GlobalHotKeys | None = None

    def register(self, combo: str, callback: Callable[[], None]) -> None:
        self._bindings[combo] = callback

    def unregister(self, combo: str) -> None:
        self._bindings.pop(combo, None)

    def start(self) -> None:
        self.stop()
        if not self._bindings:
            return
        self._listener = keyboard.GlobalHotKeys(dict(self._bindings))
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
