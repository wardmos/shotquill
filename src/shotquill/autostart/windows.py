# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Windows launch-at-login via the per-user ``Run`` registry key.

Enabling writes a string value under
``HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`` — the
canonical per-user autostart location Explorer reads at sign-in (and the one the
Task Manager *Startup* tab lists and lets users toggle). Disabling deletes the
value. ``HKCU`` (not ``HKLM``) keeps this a per-user, non-elevated change, which
matches the macOS LaunchAgent and the Linux XDG entry.

The command line and launch argv are pure helpers so they can be unit-tested on
any platform; only the three :class:`WindowsAutostartManager` methods touch the
registry, and they import ``winreg`` (Windows-only stdlib) lazily so this module
imports cleanly under the Linux/macOS test runs.
"""

from __future__ import annotations

import subprocess
import sys

from shotquill.autostart.base import AutostartManager

# The value name under the Run key. Explorer ignores the name (it runs every
# value's data); it's only the human-facing label in Task Manager's Startup tab,
# so use the product name rather than an opaque id.
RUN_VALUE_NAME = "ShotQuill"
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_arguments() -> list[str]:
    """Return the argv that re-launches this app at sign-in.

    Frozen (PyInstaller ``.exe``): the bundled executable on its own. Otherwise
    (development): the current interpreter running ``python -m shotquill``.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "shotquill"]


def build_run_command(arguments: list[str]) -> str:
    """Render ``arguments`` as a single Run-value command line.

    A ``Run`` value holds one command-line *string*, which Explorer hands to
    ``CreateProcess``. ``subprocess.list2cmdline`` applies exactly the quoting
    rules ``CreateProcess``/``CommandLineToArgvW`` parse back, so an
    interpreter path containing spaces (``C:\\Program Files\\…``) round-trips to
    one argv element instead of being split.
    """
    return subprocess.list2cmdline(arguments)


class WindowsAutostartManager(AutostartManager):
    """Manages the ``ShotQuill`` value under ``HKCU\\…\\CurrentVersion\\Run``."""

    def __init__(self, value_name: str = RUN_VALUE_NAME) -> None:
        self._value_name = value_name

    def _open_key(self, write: bool):
        """Open (creating on write) the per-user Run key.

        ``CreateKey`` is idempotent — it opens the key if it already exists — so
        it is safe for the write path even though the Run key effectively always
        exists on a real Windows install.
        """
        import winreg

        access = winreg.KEY_WRITE if write else winreg.KEY_READ
        if write:
            return winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, access)
        return winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, access)

    def is_enabled(self) -> bool:
        import winreg

        try:
            with self._open_key(write=False) as key:
                winreg.QueryValueEx(key, self._value_name)
        except FileNotFoundError:
            # Either the Run key or our value is absent — both mean "not enabled".
            return False
        return True

    def enable(self) -> None:
        import winreg

        command = build_run_command(launch_arguments())
        with self._open_key(write=True) as key:
            winreg.SetValueEx(key, self._value_name, 0, winreg.REG_SZ, command)

    def disable(self) -> None:
        import winreg

        try:
            with self._open_key(write=True) as key:
                winreg.DeleteValue(key, self._value_name)
        except FileNotFoundError:
            # Already absent (value or key) — disable is idempotent.
            pass
