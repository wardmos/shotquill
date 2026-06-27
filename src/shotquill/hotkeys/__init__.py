# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Global hotkey management (platform implementations alongside the base interface)."""

from __future__ import annotations

import os
import sys

from shotquill.hotkeys.base import HotkeyManager


def _is_wayland_session() -> bool:
    """True on a real Wayland desktop. A parallel of the probes in
    :mod:`shotquill.headless` and :mod:`shotquill.hotkeys.linux`, kept here so the
    factory can route *before* importing the X11 backend — ``import pynput`` (at
    the top of ``linux``) needs an X display and can fail outright on a pure
    Wayland session, so the portal path must not depend on it. An explicit
    ``QT_QPA_PLATFORM`` (e.g. ``offscreen`` in tests) wins."""
    if os.environ.get("QT_QPA_PLATFORM"):
        return False
    return os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(
        os.environ.get("WAYLAND_DISPLAY")
    )


def get_manager() -> HotkeyManager:
    """Pick the platform hotkey backend (the app layer's factory seam).

    macOS uses Carbon RegisterEventHotKey with hardware key-code registration
    (Option-safe); Windows likewise matches by virtual-key code (Ctrl makes the
    produced character a control code); Linux/X11 uses pynput with character
    matching; Wayland — where out-of-band key grabs are refused — uses the
    xdg-desktop-portal
    GlobalShortcuts backend. The Wayland split mirrors capture (``qtgrab`` vs
    ``wayland``); the portal backend is reached without importing the
    pynput-backed X11 module."""
    if sys.platform == "darwin":
        from shotquill.hotkeys.macos import MacHotkeyManager

        return MacHotkeyManager()
    if sys.platform.startswith("linux"):
        if _is_wayland_session():
            from shotquill.hotkeys.wayland import WaylandHotkeyManager

            return WaylandHotkeyManager()
        from shotquill.hotkeys.linux import LinuxHotkeyManager

        return LinuxHotkeyManager()
    if sys.platform.startswith("win"):
        from shotquill.hotkeys.windows import WindowsHotkeyManager

        return WindowsHotkeyManager()
    raise RuntimeError(f"no global-hotkey backend for platform {sys.platform!r}")
