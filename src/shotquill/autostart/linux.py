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


# Characters the freedesktop .desktop spec reserves inside an Exec= line; any
# of them forces the argument to be double-quoted.
_EXEC_RESERVED = set(" \t\n\"'\\><~|&;$*?#()`")


def _quote_exec_arg(arg: str) -> str:
    """Quote one Exec= argument per the freedesktop Desktop Entry spec.

    Args with no reserved char pass through bare (so a plain path stays
    readable); otherwise the arg is double-quoted and the four chars that keep
    meaning inside double quotes — ``\\ " ` $`` — are backslash-escaped, so a
    path containing a quote or ``$`` can't break out of the field or be
    interpreted by the launcher.
    """
    if not any(c in _EXEC_RESERVED for c in arg):
        return arg
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return f'"{escaped}"'


def _exec_line(arguments: list[str]) -> str:
    """Render an Exec= command line with each argument spec-quoted."""
    return " ".join(_quote_exec_arg(arg) for arg in arguments)


def build_autostart_desktop(arguments: list[str]) -> str:
    """Render an autostart ``.desktop`` entry that runs ``arguments``.

    The extra metadata (Icon/Comment/Categories/StartupNotify/GenericName) is
    what GNOME Tweaks / KDE Autostart / XFCE Session display in their startup-
    items lists; without it the entry appears with a generic icon and no
    description, leaving users unsure whether to leave it on. ``Icon=shotquill``
    is the theme-icon name; falls back gracefully when the icon isn't installed
    (e.g. dev runs without a system icon theme).

    ``GenericName`` and ``Comment`` ship with ``[zh_CN]`` siblings so a Chinese
    desktop session shows localised text in the startup-items UI — the rest of
    the app already speaks both languages and the .desktop spec resolves these
    by ``LANG`` automatically.
    """
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=ShotQuill\n"
        "GenericName=Screenshot Tool\n"
        "GenericName[zh_CN]=截图工具\n"
        "Comment=Capture and annotate screenshots from the menu bar.\n"
        "Comment[zh_CN]=从菜单栏快速截图并标注。\n"
        f"Exec={_exec_line(arguments)}\n"
        "Icon=shotquill\n"
        "Terminal=false\n"
        "Categories=Graphics;Utility;\n"
        "StartupNotify=false\n"
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
