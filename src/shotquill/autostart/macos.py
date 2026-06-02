# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""macOS launch-at-login via a per-user LaunchAgent.

Enabling writes ``~/Library/LaunchAgents/<label>.plist`` with ``RunAtLoad`` so
launchd starts ShotQuill at login; disabling removes it. We keep the plist body
and the launch command in pure helpers so they can be unit-tested without
touching the filesystem or a real ``.app`` bundle.
"""

from __future__ import annotations

import sys
from pathlib import Path
from xml.sax.saxutils import escape

from shotquill.autostart.base import AutostartManager

LAUNCH_AGENT_LABEL = "com.wardmos.shotquill"


def launch_arguments() -> list[str]:
    """Return the argv that re-launches this app.

    Frozen (PyInstaller ``.app``): the bundle's main executable. Otherwise
    (development): the current interpreter running ``python -m shotquill``.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "shotquill"]


def build_launch_agent_plist(label: str, arguments: list[str]) -> str:
    """Render a minimal LaunchAgent plist that runs ``arguments`` at login."""
    program_args = "\n".join(f"    <string>{escape(arg)}</string>" for arg in arguments)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "  <key>Label</key>\n"
        f"  <string>{escape(label)}</string>\n"
        "  <key>ProgramArguments</key>\n"
        "  <array>\n"
        f"{program_args}\n"
        "  </array>\n"
        "  <key>RunAtLoad</key>\n"
        "  <true/>\n"
        "  <key>LimitLoadToSessionType</key>\n"
        "  <string>Aqua</string>\n"
        "</dict>\n"
        "</plist>\n"
    )


class MacAutostartManager(AutostartManager):
    """Manages the LaunchAgent plist under ``~/Library/LaunchAgents``."""

    def __init__(self, label: str = LAUNCH_AGENT_LABEL) -> None:
        self._label = label
        self._plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"

    def is_enabled(self) -> bool:
        return self._plist_path.exists()

    def enable(self) -> None:
        self._plist_path.parent.mkdir(parents=True, exist_ok=True)
        contents = build_launch_agent_plist(self._label, launch_arguments())
        self._plist_path.write_text(contents, encoding="utf-8")

    def disable(self) -> None:
        self._plist_path.unlink(missing_ok=True)
