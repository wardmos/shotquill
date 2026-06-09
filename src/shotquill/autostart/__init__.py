# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Launch-at-login management (platform implementations alongside the base interface)."""

from __future__ import annotations

import sys

from shotquill.autostart.base import AutostartManager


def get_manager() -> AutostartManager:
    """Pick the platform launch-at-login backend (the app layer's factory seam).

    macOS uses a per-user LaunchAgent; Linux uses an XDG autostart ``.desktop``
    entry."""
    if sys.platform == "darwin":
        from shotquill.autostart.macos import MacAutostartManager

        return MacAutostartManager()
    if sys.platform.startswith("linux"):
        from shotquill.autostart.linux import LinuxAutostartManager

        return LinuxAutostartManager()
    raise RuntimeError(f"no autostart backend for platform {sys.platform!r}")
