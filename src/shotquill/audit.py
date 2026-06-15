# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Audit trail for programmatic (CLI / MCP) captures.

Every headless invocation is recorded — metadata only, never pixels — so the
user can always answer "which agent captured what, and when". Two sinks with
complementary properties:

1. A JSONL file (convenience layer): long-lived, greppable, but writable by
   any process running as the user.
2. The OS log (integrity layer): on macOS the ``syslog`` module feeds the
   unified log and on Linux it lands in journald/syslog — both stores are
   root-managed, so no user-space process (including an over-eager agent
   "cleaning up" after itself) can rewrite history. Retention there is
   system-controlled; the JSONL file is the durable copy.

Recording is best-effort: a failing sink must never break a capture.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

from shotquill import paths

# How far up the process tree to look when naming the caller. Enough to see
# through a shell or two to the agent host; short enough to stay cheap.
_CHAIN_LIMIT = 5

# Rotate the JSONL file once it grows past this (~20k entries): one ``.1``
# backup, overwritten on the next rotation. Disk hygiene only — tamper
# resistance comes from the OS log mirror, so losing rotated-out history to
# an attacker-with-our-uid was never a property this file could provide.
_MAX_LOG_BYTES = 5 * 1024 * 1024


def record(
    action: str,
    *,
    via: str = "cli",
    target: str | None = None,
    dest: str | None = None,
) -> None:
    """Append one audit entry to both sinks. Never raises."""
    entry = {
        "ts": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "via": via,
        "action": action,
        "target": target,
        "dest": dest,
        "caller": _caller_chain(),
        "pid": os.getpid(),
    }
    line = json.dumps(entry, ensure_ascii=False)
    try:
        log_path = paths.audit_log_path()
        _rotate_if_needed(log_path)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:  # pragma: no cover - depends on host filesystem state
        pass
    _to_system_log(line)


def _rotate_if_needed(log_path: Path) -> None:
    """Move an oversized log aside (``audit.log`` → ``audit.log.1``)."""
    try:
        if log_path.stat().st_size < _MAX_LOG_BYTES:
            return
    except OSError:  # no log yet (or unreadable): nothing to rotate
        return
    try:
        log_path.replace(log_path.with_name(log_path.name + ".1"))
    except OSError:  # pragma: no cover - rotation failure must not block entries
        pass


def _to_system_log(line: str) -> None:
    """Mirror the entry into the OS-managed (tamper-resistant) log store."""
    try:
        import syslog

        syslog.openlog("shotquill", 0, syslog.LOG_USER)
        syslog.syslog(syslog.LOG_NOTICE, line)
    except Exception:  # pragma: no cover - platform without syslog
        pass


def caller_chain() -> list[str]:
    """Public view of the ancestor process chain (the TCC "responsible process"
    approximation), so the doctor can name which process to grant Screen
    Recording. Delegates to ``_caller_chain`` so test monkeypatches still apply.
    """
    return _caller_chain()


def _caller_chain() -> list[str]:
    """Names of ancestor processes, nearest first (best effort).

    The TCC "responsible process" idea, approximated: walking past the shell
    usually reveals the agent host (Terminal, Claude Desktop, ...) that
    actually drove this capture.
    """
    chain: list[str] = []
    pid = os.getppid()
    for _ in range(_CHAIN_LIMIT):
        if pid <= 1:
            break
        name = _process_name(pid)
        if name is None:
            break
        chain.append(name)
        pid = _parent_of(pid)
    return chain


def _process_name(pid: int) -> str | None:
    try:  # Linux: free, no subprocess
        return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
    except OSError:
        return _ps(pid, "comm=")


def _parent_of(pid: int) -> int:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        # Field 4, after the parenthesized (and possibly space-laden) comm.
        return int(stat.rpartition(")")[2].split()[1])
    except (OSError, IndexError, ValueError):
        out = _ps(pid, "ppid=")
        try:
            return int(out) if out else 0
        except ValueError:
            return 0


def _ps(pid: int, column: str) -> str | None:
    """``ps`` fallback for macOS (no /proc there)."""
    if sys.platform.startswith("win"):  # pragma: no cover - not a target yet
        return None
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", column],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        ).stdout.strip()
        return out or None
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return None
