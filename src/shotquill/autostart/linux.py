# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Linux launch-at-login via an XDG autostart ``.desktop`` entry.

Enabling writes ``$XDG_CONFIG_HOME/autostart/shotquill.desktop`` (default
``~/.config/autostart``) with ``X-GNOME-Autostart-enabled=true``, which every
freedesktop-compliant desktop (GNOME, KDE, XFCE, …) runs at login; disabling
removes it. The ``.desktop`` body and the launch command are pure helpers so
they can be unit-tested without touching the filesystem.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from shotquill.autostart.base import AutostartManager

AUTOSTART_BASENAME = "shotquill.desktop"


def launch_arguments() -> list[str]:
    """Return the argv that re-launches this app at login.

    Frozen as an AppImage: the ``$APPIMAGE`` path the runtime exports (its own
    file on disk — ``sys.executable`` would be the throwaway mount point).
    Otherwise (development): the interpreter running ``python -m shotquill``.
    """
    if getattr(sys, "frozen", False):
        return [os.environ.get("APPIMAGE") or sys.executable]
    return [sys.executable, "-m", "shotquill"]


def _exec_line(arguments: list[str]) -> str:
    """Render an Exec= command line, quoting args that contain spaces (the only
    reserved char a path is likely to hit)."""
    return " ".join(f'"{arg}"' if " " in arg else arg for arg in arguments)


def build_autostart_desktop(arguments: list[str]) -> str:
    """Render a minimal autostart ``.desktop`` entry that runs ``arguments``."""
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=ShotQuill\n"
        f"Exec={_exec_line(arguments)}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def _autostart_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart"


class LinuxAutostartManager(AutostartManager):
    """Manages the autostart ``.desktop`` entry under ``~/.config/autostart``."""

    def __init__(self, basename: str = AUTOSTART_BASENAME) -> None:
        self._path = _autostart_dir() / basename

    def is_enabled(self) -> bool:
        return self._path.exists()

    def enable(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(build_autostart_desktop(launch_arguments()), encoding="utf-8")

    def disable(self) -> None:
        self._path.unlink(missing_ok=True)
