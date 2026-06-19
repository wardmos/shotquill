# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Flight-recorder cost control: frame dedup, listing, retention, and export.

All pure (no Qt): the dedup path in :func:`record.record_frame` and the
:func:`record.list_sessions` / :func:`record.prune_sessions` helpers operate on
the on-disk session format, so they're driven here with plain bytes and fixed
timestamps. The CLI surface (``record list`` / ``record prune`` / ``record frame
--dedup``) is covered against the FakeCapturer in test_record.py's style.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import pathlib
import tarfile
import zipfile

import pytest

from shotquill import cli, mcp, record

_PNG_A = b"\x89PNG\r\n\x1a\nframe-a"
_PNG_B = b"\x89PNG\r\n\x1a\nframe-b"


def _at(day: int, hour: int = 10) -> dt.datetime:
    return dt.datetime(2026, 6, day, hour, 0, 0).astimezone()


# --- frame dedup ------------------------------------------------------------


def test_dedup_references_previous_image_and_writes_nothing(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-dd", now=_at(1))
    first = record.record_frame(session, image_bytes=_PNG_A, tool="a", target="t", dedup=True)
    second = record.record_frame(session, image_bytes=_PNG_A, tool="b", target="t", dedup=True)

    # Same pixels: the second frame points at the first's file, none was written.
    assert second.image == first.image == "frames/0001.png"
    assert not (session.dir / "frames" / "0002.png").exists()
    assert second.index == 2  # still its own step in the timeline

    entries = record.load_manifest(session)["frames"]
    assert entries[1]["deduped"] is True
    assert entries[0].get("deduped") is None
    assert entries[0]["image_sha256"] == entries[1]["image_sha256"]


def test_dedup_writes_a_new_file_when_pixels_change(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-dd2", now=_at(1))
    record.record_frame(session, image_bytes=_PNG_A, tool="a", target="t", dedup=True)
    third = record.record_frame(session, image_bytes=_PNG_B, tool="b", target="t", dedup=True)

    assert third.image == "frames/0002.png"
    assert (session.dir / third.image).read_bytes() == _PNG_B


def test_dedup_is_off_by_default(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-dd3", now=_at(1))
    record.record_frame(session, image_bytes=_PNG_A, tool="a", target="t")
    second = record.record_frame(session, image_bytes=_PNG_A, tool="b", target="t")
    # Without dedup the identical frame is still written to its own file.
    assert second.image == "frames/0002.png"
    assert (session.dir / second.image).read_bytes() == _PNG_A


def test_every_frame_records_a_content_digest(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-dd4", now=_at(1))
    record.record_frame(session, image_bytes=_PNG_A, tool="a", target="t")
    import hashlib

    entry = record.load_manifest(session)["frames"][0]
    assert entry["image_sha256"] == hashlib.sha256(_PNG_A).hexdigest()


# --- listing ----------------------------------------------------------------


def _session(root, sid, *, when, complete=True, frames=1):
    session = record.start_session(records_root=root, session_id=sid, now=when)
    for _ in range(frames):
        record.record_frame(session, image_bytes=_PNG_A, tool="t", target="x", now=when)
    if complete:
        record.end_session(session, now=when)
    return session


def test_list_sessions_newest_first_with_counts(tmp_path):
    _session(tmp_path, "conv-old", when=_at(1))
    _session(tmp_path, "conv-new", when=_at(3), frames=2)

    summaries = record.list_sessions(tmp_path)
    assert [s.id for s in summaries] == ["conv-new", "conv-old"]
    assert summaries[0].frame_count == 2
    assert summaries[0].status == record.STATUS_COMPLETE
    assert summaries[0].size_bytes > 0


def test_list_skips_dirs_without_a_manifest(tmp_path):
    _session(tmp_path, "conv-real", when=_at(1))
    (tmp_path / "not-a-session").mkdir()
    (tmp_path / "stray.txt").write_text("hi")

    assert [s.id for s in record.list_sessions(tmp_path)] == ["conv-real"]


def test_list_on_missing_root_is_empty(tmp_path):
    assert record.list_sessions(tmp_path / "nope") == []


# --- pruning ----------------------------------------------------------------


def test_prune_by_max_sessions_keeps_newest(tmp_path):
    for day in (1, 2, 3, 4):
        _session(tmp_path, f"conv-{day}", when=_at(day))

    removed = record.prune_sessions(tmp_path, max_sessions=2)

    assert sorted(s.id for s in removed) == ["conv-1", "conv-2"]
    assert {s.id for s in record.list_sessions(tmp_path)} == {"conv-3", "conv-4"}


def test_prune_by_max_age(tmp_path):
    _session(tmp_path, "conv-stale", when=_at(1))
    _session(tmp_path, "conv-fresh", when=_at(9))

    # "now" is day 10; 5-day window drops the day-1 session, keeps day-9.
    removed = record.prune_sessions(tmp_path, max_age_days=5, now=_at(10))

    assert [s.id for s in removed] == ["conv-stale"]
    assert {s.id for s in record.list_sessions(tmp_path)} == {"conv-fresh"}


def test_prune_never_touches_a_recording_session(tmp_path):
    # An in-flight (not ended) session is the oldest, but must survive pruning.
    _session(tmp_path, "conv-live", when=_at(1), complete=False)
    _session(tmp_path, "conv-done", when=_at(2))

    removed = record.prune_sessions(tmp_path, max_sessions=1)

    assert [s.id for s in removed] == []  # only complete sessions are candidates
    assert (tmp_path / "conv-live").exists()


def test_prune_dry_run_reports_without_deleting(tmp_path):
    _session(tmp_path, "conv-1", when=_at(1))
    _session(tmp_path, "conv-2", when=_at(2))

    removed = record.prune_sessions(tmp_path, max_sessions=1, dry_run=True)

    assert [s.id for s in removed] == ["conv-1"]
    assert (tmp_path / "conv-1").exists()  # nothing actually deleted


# --- CLI --------------------------------------------------------------------


def test_cli_list_and_prune_json(tmp_path, monkeypatch, capsys):
    from shotquill import paths

    monkeypatch.setattr(paths, "records_dir", lambda: tmp_path / "records")
    root = tmp_path / "records"
    _session(root, "conv-1", when=_at(1))
    _session(root, "conv-2", when=_at(2))

    assert cli.main(["record", "list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [s["conversation_id"] for s in listed] == ["conv-2", "conv-1"]

    assert cli.main(["record", "prune", "--max-sessions", "1", "--json"]) == 0
    pruned = json.loads(capsys.readouterr().out)
    assert pruned["removed"] == ["conv-1"]
    assert pruned["count"] == 1
    assert not (root / "conv-1").exists()


def test_cli_prune_requires_a_limit(tmp_path, monkeypatch, capsys):
    from shotquill import paths

    monkeypatch.setattr(paths, "records_dir", lambda: tmp_path / "records")
    # No --max-age-days / --max-sessions: a usage error, nothing deleted.
    assert cli.main(["record", "prune"]) != 0


# --- export: aggregate_pii + export_session (pure) --------------------------


def test_aggregate_pii_sums_flags_across_frames(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-agg", now=_at(1))
    record.record_frame(session, image_bytes=_PNG_A, tool="t", target="x")
    record.record_frame(
        session, image_bytes=_PNG_B, tool="t", target="x", pii=[{"kind": "email", "count": 2}]
    )
    record.record_frame(
        session,
        image_bytes=_PNG_A + b"!",
        tool="t",
        target="x",
        pii=[{"kind": "email", "count": 1}, {"kind": "ssn", "count": 3}],
    )
    assert record.aggregate_pii(record.load_manifest(session)) == {"email": 3, "ssn": 3}


def test_aggregate_pii_empty_without_flags(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-noagg", now=_at(1))
    record.record_frame(session, image_bytes=_PNG_A, tool="t", target="x")
    assert record.aggregate_pii(record.load_manifest(session)) == {}


def test_export_session_bundles_everything_under_one_folder(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-exp", now=_at(1))
    record.record_frame(session, image_bytes=_PNG_A, tool="t", target="x", now=_at(1))
    record.end_session(session, now=_at(1))
    archive = record.export_session(session)
    assert archive == tmp_path / "conv-exp.tar.gz"  # default path beside the session
    names = sorted(tarfile.open(archive).getnames())
    assert "conv-exp/manifest.json" in names
    assert "conv-exp/frames/0001.png" in names
    assert "conv-exp/index.html" in names  # the filmstrip rode along


def test_export_session_zip_and_custom_output_preserve_bytes(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-z", now=_at(1))
    record.record_frame(session, image_bytes=_PNG_A, tool="t", target="x", now=_at(1))
    out = tmp_path / "bundle.zip"
    archive = record.export_session(session, out, fmt="zip")
    assert archive == out
    with zipfile.ZipFile(archive) as zf:
        assert zf.read("conv-z/frames/0001.png") == _PNG_A


def test_export_session_rejects_unknown_format(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-bad", now=_at(1))
    with pytest.raises(record.RecordError, match="format"):
        record.export_session(session, fmt="rar")


# --- export: CLI + MCP surfaces ---------------------------------------------


def _isolate(monkeypatch, tmp_path):
    from shotquill import audit, paths

    monkeypatch.setattr(paths, "records_dir", lambda: tmp_path / "records")
    monkeypatch.setattr(paths, "audit_log_path", lambda: tmp_path / "audit.log")
    monkeypatch.setattr(audit, "_to_system_log", lambda line: None)
    monkeypatch.setattr(audit, "_caller_chain", lambda: ["pytest"])
    return tmp_path / "records"


def _flagged_session(root, sid):
    session = record.start_session(records_root=root, session_id=sid, now=_at(1))
    record.record_frame(
        session, image_bytes=_PNG_A, tool="t", target="x", pii=[{"kind": "email", "count": 1}]
    )
    record.end_session(session, now=_at(1))
    return session


def test_cli_record_export_prints_archive_path(tmp_path, monkeypatch, capsys):
    root = _isolate(monkeypatch, tmp_path)
    _session(root, "conv-e", when=_at(1))
    assert cli.main(["record", "export", "conv-e"]) == 0
    out = capsys.readouterr().out.strip()
    assert out.endswith("conv-e.tar.gz")
    assert pathlib.Path(out).is_file()


def test_cli_record_export_fail_on_pii_blocks_and_writes_nothing(tmp_path, monkeypatch, capsys):
    root = _isolate(monkeypatch, tmp_path)
    _flagged_session(root, "conv-g")
    rc = cli.main(["record", "export", "conv-g", "--fail-on-pii"])
    assert rc == 6  # EXIT_BLOCKED
    assert "likely PII" in capsys.readouterr().err
    assert not (root / "conv-g.tar.gz").exists()  # refused before writing


def test_cli_record_export_fail_on_pii_ok_when_clean(tmp_path, monkeypatch, capsys):
    root = _isolate(monkeypatch, tmp_path)
    _session(root, "conv-clean", when=_at(1))  # no PII flags
    assert cli.main(["record", "export", "conv-clean", "--fail-on-pii"]) == 0


def _mcp_export(args):
    raw = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "record_export", "arguments": args},
        }
    )
    fout = io.StringIO()
    mcp.serve(stdin=io.StringIO(raw + "\n"), stdout=fout)
    return json.loads(fout.getvalue())["result"]


def test_mcp_record_export_returns_archive_and_pii(tmp_path, monkeypatch):
    root = _isolate(monkeypatch, tmp_path)
    _flagged_session(root, "conv-mcp-e")
    result = _mcp_export({"session": "conv-mcp-e"})
    assert result["isError"] is False
    structured = result["structuredContent"]
    assert structured["archive"].endswith("conv-mcp-e.tar.gz")
    assert structured["pii"] == {"email": 1}  # residual risk reported


def test_mcp_record_export_fail_on_pii_is_blocked(tmp_path, monkeypatch):
    root = _isolate(monkeypatch, tmp_path)
    _flagged_session(root, "conv-mcp-g")
    result = _mcp_export({"session": "conv-mcp-g", "fail_on_pii": True})
    assert result["isError"] is True
    assert json.loads(result["content"][0]["text"])["type"] == "blocked"
