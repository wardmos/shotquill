# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Platform-specific directories for headless (CLI / MCP) output and logs.

Kept behind functions — not constants — so the Linux/Wayland ports only have
to change one module, and tests can monkeypatch a single seam.
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path


def capture_tmp_dir() -> Path:
    """Default destination for headless captures: a private per-user temp dir.

    Agent captures are working artifacts, not keepsakes — they default to the
    system temp dir (auto-cleaned) instead of the user's curated screenshot
    folder. ``0o700`` because the temp root is shared and screenshots are
    sensitive.
    """
    directory = Path(tempfile.gettempdir()) / "shotquill"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    # ``mode=`` only applies at creation, and ``exist_ok`` accepts whatever is
    # already there — including a directory (or symlink) squatted at this
    # well-known name by another user of the shared temp root, which would
    # quietly receive every capture. Refuse anything we don't own outright,
    # and re-tighten permissions that have drifted open.
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise OSError(f"{directory} is not a directory; refusing to write captures there")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise OSError(f"{directory} is owned by another user; refusing to write captures there")
    if stat.S_IMODE(info.st_mode) & 0o077:
        directory.chmod(0o700)
    return directory


def config_dir() -> Path:
    """User configuration directory for the headless surface (created).

    macOS uses ``~/Library/Application Support``; elsewhere we follow XDG,
    honoring ``$XDG_CONFIG_HOME``. Separate from the audit log (state) and the
    capture temp dir (cache) so each lands where its platform expects.
    """
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", "") or Path.home() / ".config")
    directory = base / "shotquill"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def blocklist_path() -> Path:
    """The app blocklist file (apps that must never be captured).

    A plain JSON file the GUI, CLI, and MCP server all read, so a rule added in
    Settings takes effect for programmatic captures too. Hand-editable on
    purpose — users can ``cat`` and grep what they are protected against.
    """
    return config_dir() / "blocklist.json"


def audit_log_path() -> Path:
    """Where the JSONL audit log lives (parent directory is created).

    macOS uses ``~/Library/Logs`` (visible in Console.app); elsewhere we follow
    XDG and put state where it belongs, honoring ``$XDG_STATE_HOME``.
    """
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Logs"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", "") or Path.home() / ".local" / "state")
    directory = base / "shotquill"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "audit.log"
