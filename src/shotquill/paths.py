# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Platform-specific directories for headless (CLI / MCP) output and logs.

Kept behind functions — not constants — so the Linux/Wayland ports only have
to change one module, and tests can monkeypatch a single seam.
"""

from __future__ import annotations

import os
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
    return directory


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
