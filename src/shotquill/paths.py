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
    """User configuration directory for the headless surface (computed, not created).

    macOS uses ``~/Library/Application Support``; Windows uses roaming
    ``%APPDATA%`` (where per-user app config conventionally lives); elsewhere we
    follow XDG, honoring ``$XDG_CONFIG_HOME``. Separate from the audit log
    (state) and the capture temp dir (cache) so each lands where its platform
    expects. It is not created here — the config is read far more often than
    written (every capture consults the blocklist), so creating the directory
    is the writer's job (see ``blocklist.save``), not a side effect of every
    read.
    """
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", "") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", "") or Path.home() / ".config")
    return base / "shotquill"


def data_dir() -> Path:
    """User data directory for durable headless artifacts (computed, not created).

    Distinct from :func:`config_dir` (preferences, read on every capture) and
    :func:`capture_tmp_dir` (auto-cleaned cache): flight-recorder sessions are
    archives the user keeps, so they belong in the platform's app-data location.
    macOS uses ``~/Library/Application Support``; Windows uses machine-local
    ``%LOCALAPPDATA%`` (artifacts are local state, not roaming); elsewhere we
    follow XDG, honoring ``$XDG_DATA_HOME``. Created by the writer, not here.
    """
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA", "") or Path.home() / "AppData" / "Local"
        base = Path(local)
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", "") or Path.home() / ".local" / "share")
    return base / "shotquill"


def records_dir() -> Path:
    """Default root for flight-recorder sessions (``<data>/records``).

    Each ``squill session start`` creates one ``<records>/<session-id>/`` beneath
    it (unless the caller pins an explicit directory). Not created here — the
    session writer makes the leaf directory it owns.
    """
    return data_dir() / "records"


def blocklist_path() -> Path:
    """The app blocklist file (apps that must never be captured).

    A plain JSON file the GUI, CLI, and MCP server all read, so a rule added in
    Settings takes effect for programmatic captures too. Hand-editable on
    purpose — users can ``cat`` and grep what they are protected against.
    """
    return config_dir() / "blocklist.json"


def allowlist_path() -> Path:
    """The capture allowlist file (when enabled, only these apps are captured).

    The complement of the blocklist and, like it, a plain hand-editable JSON
    file the GUI, CLI, and MCP server all read. It carries its own ``enabled``
    flag (default off) inside the file rather than in QSettings, so the headless
    surface enforces it without a running Qt application — the same reason the
    blocklist lives in a file.
    """
    return config_dir() / "allowlist.json"


def audit_log_path() -> Path:
    """Where the JSONL audit log lives (parent directory is created).

    macOS uses ``~/Library/Logs`` (visible in Console.app); Windows uses
    machine-local ``%LOCALAPPDATA%`` (logs are local state, not roaming config);
    elsewhere we follow XDG and put state where it belongs, honoring
    ``$XDG_STATE_HOME``.
    """
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Logs"
    elif sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA", "") or Path.home() / "AppData" / "Local"
        directory = Path(local) / "shotquill" / "Logs"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / "audit.log"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", "") or Path.home() / ".local" / "state")
    directory = base / "shotquill"
    # ``0o700``: the audit log records what was captured, when, and by which
    # process — metadata other local users have no business reading.
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory / "audit.log"
