# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Global hotkey management (platform implementations alongside the base interface)."""

from __future__ import annotations

import sys

from shotquill.hotkeys.base import HotkeyManager


def get_manager() -> HotkeyManager:
    """Pick the platform hotkey backend (the app layer's factory seam).

    macOS uses pynput with hardware key-code matching (Option-safe); Linux/X11
    uses pynput with character matching. Wayland has no global-grab backend yet,
    so it raises — the app falls back to menu-driven capture there."""
    if sys.platform == "darwin":
        from shotquill.hotkeys.macos import MacHotkeyManager

        return MacHotkeyManager()
    if sys.platform.startswith("linux"):
        from shotquill.hotkeys.linux import LinuxHotkeyManager

        return LinuxHotkeyManager()
    raise RuntimeError(f"no global-hotkey backend for platform {sys.platform!r}")
