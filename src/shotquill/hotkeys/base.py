# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Abstract interface for system-wide hotkey registration.

Platform implementations (e.g. ``macos.py``) live alongside this module so new
platforms can be added without touching the app layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable


class HotkeyManager(ABC):
    """Registers global hotkeys and invokes callbacks when they are pressed."""

    @abstractmethod
    def register(self, combo: str, callback: Callable[[], None]) -> None:
        """Bind a pynput-style combo (e.g. ``<alt>+a``) to a callback."""

    @abstractmethod
    def unregister(self, combo: str) -> None:
        """Remove a previously registered combo."""

    @abstractmethod
    def start(self) -> None:
        """Begin listening for hotkeys (non-blocking)."""

    @abstractmethod
    def stop(self) -> None:
        """Stop listening and release resources."""
