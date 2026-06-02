# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Abstract interface for "launch at login" (start the app when the user logs in).

Platform implementations (e.g. ``macos.py``) live alongside this module so new
platforms can be added without touching the app layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class AutostartManager(ABC):
    """Installs or removes a per-user "launch at login" entry for the app."""

    @abstractmethod
    def is_enabled(self) -> bool:
        """Return whether launch-at-login is currently installed."""

    @abstractmethod
    def enable(self) -> None:
        """Install the login entry (idempotent)."""

    @abstractmethod
    def disable(self) -> None:
        """Remove the login entry (idempotent)."""

    def set_enabled(self, enabled: bool) -> None:
        """Install or remove the login entry to match ``enabled``."""
        if enabled:
            self.enable()
        else:
            self.disable()
