# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Audit trail: entry shape and the (real, unmocked) caller-chain walk."""

from __future__ import annotations

import datetime as dt
import json

from shotquill import audit, paths


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
