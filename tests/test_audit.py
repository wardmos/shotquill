# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Audit trail: entry shape and the (real, unmocked) caller-chain walk."""

from __future__ import annotations

import datetime as dt
import json
import sys

import pytest

from shotquill import audit, paths

# The process-name and caller-chain walks read /proc (Linux) or shell out to
# ``ps`` (macOS); Windows has neither, so audit degrades to empty there by
# design. These tests assert the populated POSIX behaviour.
_posix_only = pytest.mark.skipif(
    sys.platform.startswith("win"), reason="POSIX /proc or ps process introspection"
)


def test_record_entry_shape(tmp_path, monkeypatch):
    log = tmp_path / "audit.log"
    system_lines: list[str] = []
    monkeypatch.setattr(paths, "audit_log_path", lambda: log)
    monkeypatch.setattr(audit, "_to_system_log", system_lines.append)

    audit.record("capture", via="cli", target="fullscreen", dest="/tmp/x.png")

    (line,) = log.read_text(encoding="utf-8").splitlines()
    entry = json.loads(line)
    assert entry["action"] == "capture"
    assert entry["via"] == "cli"
    assert entry["target"] == "fullscreen"
    assert entry["dest"] == "/tmp/x.png"
    assert isinstance(entry["pid"], int)
    # Timestamp is ISO-8601 with a UTC offset, so entries stay comparable
    # across machines and DST changes.
    assert dt.datetime.fromisoformat(entry["ts"]).utcoffset() is not None
    # The same line went to the tamper-resistant sink.
    assert system_lines == [line]


def test_record_appends_instead_of_truncating(tmp_path, monkeypatch):
    log = tmp_path / "audit.log"
    monkeypatch.setattr(paths, "audit_log_path", lambda: log)
    monkeypatch.setattr(audit, "_to_system_log", lambda line: None)

    audit.record("capture", via="cli")
    audit.record("ocr", via="mcp")

    lines = log.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["action"] for line in lines] == ["capture", "ocr"]
    assert [json.loads(line)["via"] for line in lines] == ["cli", "mcp"]


def test_record_defaults_and_optional_fields(tmp_path, monkeypatch):
    log = tmp_path / "audit.log"
    monkeypatch.setattr(paths, "audit_log_path", lambda: log)
    monkeypatch.setattr(audit, "_to_system_log", lambda line: None)

    audit.record("windows")

    entry = json.loads(log.read_text(encoding="utf-8"))
    # via defaults to the CLI; absent target/dest are recorded as null, not
    # omitted, so the JSONL schema stays stable for log consumers.
    assert entry["via"] == "cli"
    assert entry["target"] is None
    assert entry["dest"] is None


def test_record_preserves_unicode_verbatim(tmp_path, monkeypatch):
    # Window titles are user data and often Chinese; ensure_ascii=False keeps
    # the log greppable for them without \uXXXX escapes.
    log = tmp_path / "audit.log"
    monkeypatch.setattr(paths, "audit_log_path", lambda: log)
    monkeypatch.setattr(audit, "_to_system_log", lambda line: None)

    audit.record("capture", target="微信 — 聊天窗口")

    raw = log.read_text(encoding="utf-8")
    assert "微信 — 聊天窗口" in raw
    assert json.loads(raw)["target"] == "微信 — 聊天窗口"


def test_caller_chain_respects_chain_limit(monkeypatch):
    # An infinitely deep (synthetic) process tree must not loop forever.
    monkeypatch.setattr(audit, "_process_name", lambda pid: f"proc-{pid}")
    monkeypatch.setattr(audit, "_parent_of", lambda pid: pid + 1)
    monkeypatch.setattr(audit.os, "getppid", lambda: 1000)

    chain = audit._caller_chain()

    assert len(chain) == audit._CHAIN_LIMIT
    assert chain[0] == "proc-1000"


def test_caller_chain_stops_at_init(monkeypatch):
    # Reaching pid 1 (or 0, post-reparent) ends the walk without naming init.
    monkeypatch.setattr(audit.os, "getppid", lambda: 1)
    assert audit._caller_chain() == []


def test_caller_chain_stops_when_name_unresolvable(monkeypatch):
    # A vanished ancestor (raced exit) truncates the chain instead of raising.
    monkeypatch.setattr(audit.os, "getppid", lambda: 4242)
    monkeypatch.setattr(audit, "_process_name", lambda pid: None)
    assert audit._caller_chain() == []


def test_parent_of_nonexistent_pid_returns_zero():
    # 2**22 exceeds the default pid_max; both /proc and `ps` will miss, and the
    # fallback contract is 0 ("stop walking"), never an exception.
    assert audit._parent_of(2**22 + 1) == 0


@_posix_only
def test_process_name_resolves_own_process():
    name = audit._process_name(audit.os.getpid())
    assert name is not None
    assert "python" in name.lower() or "pytest" in name.lower()


@_posix_only
def test_caller_chain_walks_real_processes():
    chain = audit._caller_chain()
    # At minimum the immediate parent (pytest's interpreter or a shell).
    assert chain
    assert all(isinstance(name, str) and name for name in chain)


def test_record_survives_unwritable_log(tmp_path, monkeypatch):
    def _denied():
        raise OSError("read-only filesystem")

    monkeypatch.setattr(paths, "audit_log_path", _denied)
    monkeypatch.setattr(audit, "_to_system_log", lambda line: None)
    audit.record("capture", via="cli")  # must not raise


def test_rotation_moves_oversized_log_aside(tmp_path, monkeypatch):
    log = tmp_path / "audit.log"
    old_line = '{"old": true}\n'
    log.write_text(old_line * 3, encoding="utf-8")
    monkeypatch.setattr(paths, "audit_log_path", lambda: log)
    monkeypatch.setattr(audit, "_to_system_log", lambda line: None)
    monkeypatch.setattr(audit, "_caller_chain", lambda: ["pytest"])
    monkeypatch.setattr(audit, "_MAX_LOG_BYTES", len(old_line))  # force rotation

    audit.record("capture", via="cli", target="fullscreen")

    backup = tmp_path / "audit.log.1"
    assert backup.read_text(encoding="utf-8") == old_line * 3  # history preserved
    (entry,) = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert entry["action"] == "capture"  # fresh file holds only the new entry


def test_rotation_overwrites_previous_backup(tmp_path, monkeypatch):
    log = tmp_path / "audit.log"
    log.write_text("current\n", encoding="utf-8")
    (tmp_path / "audit.log.1").write_text("ancient\n", encoding="utf-8")
    monkeypatch.setattr(paths, "audit_log_path", lambda: log)
    monkeypatch.setattr(audit, "_to_system_log", lambda line: None)
    monkeypatch.setattr(audit, "_caller_chain", lambda: ["pytest"])
    monkeypatch.setattr(audit, "_MAX_LOG_BYTES", 1)

    audit.record("capture", via="cli")

    assert (tmp_path / "audit.log.1").read_text(encoding="utf-8") == "current\n"


def test_no_rotation_below_threshold(tmp_path, monkeypatch):
    log = tmp_path / "audit.log"
    log.write_text("small\n", encoding="utf-8")
    monkeypatch.setattr(paths, "audit_log_path", lambda: log)
    monkeypatch.setattr(audit, "_to_system_log", lambda line: None)
    monkeypatch.setattr(audit, "_caller_chain", lambda: ["pytest"])

    audit.record("capture", via="cli")

    assert not (tmp_path / "audit.log.1").exists()
    assert log.read_text(encoding="utf-8").startswith("small\n")  # appended, not replaced
