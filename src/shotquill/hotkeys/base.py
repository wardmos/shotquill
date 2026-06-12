# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Abstract interface for system-wide hotkey registration.

Platform implementations (e.g. ``macos.py``) live alongside this module so new
platforms can be added without touching the app layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable


class HotkeyUnavailable(RuntimeError):
    """The session refuses global hotkey grabs (e.g. Wayland blocks them).

    Distinct from ``PermissionError`` (which means "ask the user for a grant
    we can guide them to"): there is nothing actionable from inside the app
    here, so the caller should surface the reason and keep the menu/tray
    actions working as the fallback path. ``reason`` is a human-readable
    explanation the app can show in a notification."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class HotkeyManager(ABC):
    """Registers global hotkeys and invokes callbacks when they are pressed."""

    @abstractmethod
    def register(
        self, combo: str, callback: Callable[[], None], description: str | None = None
    ) -> None:
        """Bind a pynput-style combo (e.g. ``<alt>+a``) to a callback.

        ``description`` is a human-readable label for the action (e.g. "Smart
        capture"). The pynput backends ignore it — they grab keys directly — but
        the Wayland GlobalShortcuts backend hands it to the portal, which shows
        it in the compositor's own shortcuts settings, so the user can see and
        re-bind the action there."""

    @abstractmethod
    def unregister(self, combo: str) -> None:
        """Remove a previously registered combo."""

    @abstractmethod
    def clear(self) -> None:
        """Remove all registered combos (used before re-applying settings)."""

    @abstractmethod
    def start(self) -> None:
        """Begin listening for hotkeys (non-blocking)."""

    @abstractmethod
    def stop(self) -> None:
        """Stop listening and release resources."""
