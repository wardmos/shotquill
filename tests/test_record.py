# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Flight-recorder tests: the session format (pure) and the CLI round trip.

The format logic in :mod:`shotquill.record` owns no pixels, so most of this
file feeds it plain bytes and asserts on the manifest / filmstrip without a
screen. The CLI section drives ``record start|frame|end`` against an in-memory
FakeCapturer, mirroring test_cli.py.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from shotquill import audit, cli, headless, paths, record
from shotquill.capture.base import CaptureResult, DisplayInfo, Rect, WindowInfo

_FAKE_PNG = b"\x89PNG\r\n\x1a\nfake-bytes"
_FIXED = dt.datetime(2026, 6, 13, 10, 0, 3).astimezone()


# --- session format (pure, no Qt) -------------------------------------------


def test_start_writes_manifest_with_otel_fields(tmp_path):
    session = record.start_session(
        records_root=tmp_path,
        session_id="conv-test-1",
        agent_name="builder",
        agent_id="agent-7",
        label="login flow",
        now=_FIXED,
    )
    assert session.dir == tmp_path / "conv-test-1"
    assert session.frames_dir.is_dir()
    manifest = json.loads(session.manifest_path.read_text(encoding="utf-8"))
    assert manifest["shotquill_manifest_version"] == record.MANIFEST_VERSION
    assert manifest["conversation_id"] == "conv-test-1"
    assert manifest["agent"] == {"name": "builder", "id": "agent-7"}
    assert manifest["status"] == record.STATUS_RECORDING
    assert manifest["label"] == "login flow"
    assert manifest["started_at"].startswith("2026-06-13T10:00:03")
    assert manifest["ended_at"] is None
    assert manifest["frames"] == []


def test_start_pins_explicit_directory(tmp_path):
    target = tmp_path / "ci-artifacts" / "run-42"
    session = record.start_session(directory=target, session_id="conv-x", now=_FIXED)
    assert session.dir == target
    assert session.manifest_path.is_file()


def test_new_session_id_format():
    sid = record.new_session_id(_FIXED, suffix="abc123")
    assert sid == "conv-20260613-100003-abc123"


def test_record_frame_appends_and_writes_image(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-1", now=_FIXED)
    frame = record.record_frame(
        session,
        image_bytes=_FAKE_PNG,
        tool="click",
        target="window 33",
        label="click submit",
        redacted=True,
        now=_FIXED,
    )
    assert frame.index == 1
    assert frame.image == "frames/0001.png"
    assert (session.dir / frame.image).read_bytes() == _FAKE_PNG

    manifest = record.load_manifest(session)
    assert len(manifest["frames"]) == 1
    entry = manifest["frames"][0]
    assert entry["span"]["tool_name"] == "click"
    assert entry["span"]["tool_call_id"] == "conv-1/frame/1"
    assert entry["label"] == "click submit"
    assert entry["target"] == "window 33"
    assert entry["redacted"] is True


def test_record_frame_increments_index(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-2", now=_FIXED)
    record.record_frame(session, image_bytes=_FAKE_PNG, tool="a", target="t")
    second = record.record_frame(session, image_bytes=_FAKE_PNG, tool="b", target="t")
    assert second.index == 2
    assert second.image == "frames/0002.png"
    assert len(record.load_manifest(session)["frames"]) == 2


def test_frame_after_end_is_refused(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-3", now=_FIXED)
    record.end_session(session, now=_FIXED)
    with pytest.raises(record.RecordError, match="already closed"):
        record.record_frame(session, image_bytes=_FAKE_PNG, tool="a", target="t")


def test_end_marks_complete_and_renders_filmstrip(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-4", now=_FIXED)
    record.record_frame(session, image_bytes=_FAKE_PNG, tool="open", target="fullscreen")
    html_path = record.end_session(session, now=_FIXED)

    assert html_path == session.filmstrip_path
    manifest = record.load_manifest(session)
    assert manifest["status"] == record.STATUS_COMPLETE
    assert manifest["ended_at"].startswith("2026-06-13T10:00:03")
    html = html_path.read_text(encoding="utf-8")
    assert "frames/0001.png" in html
    assert "1 frame(s)" in html

    # end also drops the OTLP/JSON projection next to the filmstrip.
    otlp_doc = json.loads(session.otlp_path.read_text(encoding="utf-8"))
    spans = otlp_doc["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert [s["name"] for s in spans] == ["invoke_agent", "execute_tool open"]


def test_resolve_by_id_and_by_path(tmp_path):
    record.start_session(records_root=tmp_path, session_id="conv-5", now=_FIXED)
    by_id = record.resolve_session("conv-5", records_root=tmp_path)
    by_path = record.resolve_session(str(tmp_path / "conv-5"))
    assert by_id.id == by_path.id == "conv-5"


def test_resolve_missing_session_raises(tmp_path):
    with pytest.raises(record.SessionNotFound):
        record.resolve_session("nope", records_root=tmp_path)


def test_corrupt_manifest_raises(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-6", now=_FIXED)
    session.manifest_path.write_text("{not json", encoding="utf-8")
    with pytest.raises(record.RecordError, match="corrupt"):
        record.load_manifest(session)


def test_unknown_manifest_version_raises(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-7", now=_FIXED)
    data = json.loads(session.manifest_path.read_text(encoding="utf-8"))
    data["shotquill_manifest_version"] = 999
    session.manifest_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(record.RecordError, match="version"):
        record.load_manifest(session)


# --- filmstrip rendering ----------------------------------------------------


def test_filmstrip_escapes_app_supplied_strings():
    manifest = {
        "shotquill_manifest_version": 1,
        "conversation_id": "conv-x",
        "agent": {"name": None, "id": None},
        "status": "complete",
        "started_at": "2026-06-13T10:00:00",
        "ended_at": "2026-06-13T10:01:00",
        "frames": [
            {
                "span": {"tool_name": "click", "tool_call_id": "conv-x/frame/1"},
                "at": "2026-06-13T10:00:03",
                "label": "<script>alert(1)</script>",
                "image": "frames/0001.png",
                "target": "window <b>Mail</b>",
                "redacted": True,
            }
        ],
    }
    html = record.render_filmstrip(manifest)
    # The injected markup is escaped, not live.
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "window &lt;b&gt;Mail&lt;/b&gt;" in html
    assert "redacted" in html  # the badge renders for a redacted frame


def test_filmstrip_handles_empty_session():
    manifest = {
        "shotquill_manifest_version": 1,
        "conversation_id": "conv-empty",
        "agent": {"name": None, "id": None},
        "status": "complete",
        "started_at": "2026-06-13T10:00:00",
        "ended_at": None,
        "frames": [],
    }
    html = record.render_filmstrip(manifest)
    assert "No frames recorded." in html


# --- CLI round trip ---------------------------------------------------------


def _result(width: int = 2, height: int = 2) -> CaptureResult:
    return CaptureResult(
        width=width, height=height, scale=1.0, pixels=bytes([255, 0, 0, 255] * width * height)
    )


class FakeCapturer:
    def __init__(self) -> None:
        self.include_cursor = False
        self.windows = [
            WindowInfo(window_id=33, owner="Notes", title="Scratch", bounds=Rect(5, 5, 300, 200)),
        ]
        self.displays = [
            DisplayInfo(index=0, name="built-in", bounds=Rect(0, 0, 1440, 900), primary=True),
        ]

    def capture_fullscreen(self, exclude_window_ids=frozenset()) -> CaptureResult:
        return _result()

    def capture_region(self, region: Rect) -> CaptureResult:
        return _result()

    def capture_window(self, window_id: int) -> CaptureResult:
        return _result()

    def list_windows(self) -> list[WindowInfo]:
        return self.windows

    def list_displays(self) -> list[DisplayInfo]:
        return self.displays


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """Keep records, audit, and capture temp inside tmp and off syslog."""
    monkeypatch.setattr(paths, "records_dir", lambda: tmp_path / "records")
    monkeypatch.setattr(paths, "audit_log_path", lambda: tmp_path / "audit.log")
    monkeypatch.setattr(audit, "_to_system_log", lambda line: None)
    monkeypatch.setattr(audit, "_caller_chain", lambda: ["pytest"])


@pytest.fixture
def fake_capturer(monkeypatch):
    pytest.importorskip("PySide6")
    capturer = FakeCapturer()
    monkeypatch.setattr(headless, "get_capturer", lambda include_cursor=False: capturer)
    return capturer


def _empty_blocklist(monkeypatch):
    from shotquill import blocklist as bl

    monkeypatch.setattr(headless, "active_blocklist", lambda: bl.Blocklist(()))


def test_cli_round_trip(fake_capturer, monkeypatch, capsys, tmp_path):
    _empty_blocklist(monkeypatch)

    assert cli.main(["record", "start", "--id", "conv-cli", "--agent", "builder"]) == 0
    session_dir = capsys.readouterr().out.strip()
    assert session_dir.endswith("conv-cli")

    assert (
        cli.main(
            ["record", "frame", "--session", session_dir, "--tool", "click", "--label", "submit"]
        )
        == 0
    )
    image_path = capsys.readouterr().out.strip()
    # The returned path is absolute and uses native separators (backslashes on
    # Windows); the stored relative field stays forward-slashed.
    assert image_path.replace("\\", "/").endswith("frames/0001.png")
    with open(image_path, "rb") as fh:
        assert fh.read(4) == b"\x89PNG"

    assert cli.main(["record", "end", "--session", session_dir]) == 0
    html_path = capsys.readouterr().out.strip()
    assert html_path.endswith("index.html")

    manifest = json.loads((tmp_path / "records" / "conv-cli" / "manifest.json").read_text())
    assert manifest["status"] == "complete"
    assert len(manifest["frames"]) == 1
    assert manifest["frames"][0]["redacted"] is False  # empty blocklist


def test_cli_frame_resolves_id_against_default_root(fake_capturer, monkeypatch, capsys):
    _empty_blocklist(monkeypatch)
    cli.main(["record", "start", "--id", "conv-byid"])
    capsys.readouterr()
    # Thread the bare id (not the path) back; it resolves under records_dir().
    assert cli.main(["record", "frame", "--session", "conv-byid", "--tool", "type"]) == 0


def test_cli_frame_redacted_flag_tracks_blocklist(fake_capturer, monkeypatch, capsys):
    from shotquill import blocklist as bl

    monkeypatch.setattr(
        headless, "active_blocklist", lambda: bl.Blocklist((bl.BlockRule(name="1Password"),))
    )
    cli.main(["record", "start", "--id", "conv-redact"])
    session_dir = capsys.readouterr().out.strip()
    assert cli.main(["record", "frame", "--session", session_dir, "--tool", "click", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["redacted"] is True


def test_cli_frame_missing_session_is_error(fake_capturer, monkeypatch, capsys):
    _empty_blocklist(monkeypatch)
    assert cli.main(["record", "frame", "--session", "ghost", "--tool", "click"]) == 1
    assert "no recording session" in capsys.readouterr().err


def test_cli_frame_dedup_references_previous(fake_capturer, monkeypatch, capsys, tmp_path):
    # The FakeCapturer returns identical pixels each call; deterministic encoding
    # makes them byte-identical, so --dedup files the second frame as a reference.
    _empty_blocklist(monkeypatch)
    cli.main(["record", "start", "--id", "conv-dedup"])
    session_dir = capsys.readouterr().out.strip()
    cli.main(["record", "frame", "--session", session_dir, "--tool", "a", "--dedup"])
    cli.main(["record", "frame", "--session", session_dir, "--tool", "b", "--dedup"])
    capsys.readouterr()

    frames_dir = tmp_path / "records" / "conv-dedup" / "frames"
    assert (frames_dir / "0001.png").exists()
    assert not (frames_dir / "0002.png").exists()  # second deduped to the first
    manifest = json.loads((tmp_path / "records" / "conv-dedup" / "manifest.json").read_text())
    assert manifest["frames"][1]["deduped"] is True


def test_cli_frame_max_dimension_shrinks_the_stored_image(fake_capturer, monkeypatch, capsys):
    from PySide6.QtGui import QImage

    _empty_blocklist(monkeypatch)
    cli.main(["record", "start", "--id", "conv-small"])
    session_dir = capsys.readouterr().out.strip()
    # FakeCapturer yields a 2x2 frame; cap the long edge to 1 -> a 1x1 archive.
    cli.main(["record", "frame", "--session", session_dir, "--tool", "a", "--max-dimension", "1"])
    image_path = capsys.readouterr().out.strip()
    stored = QImage(image_path)
    assert (stored.width(), stored.height()) == (1, 1)


def test_cli_record_audits_via_record(fake_capturer, monkeypatch, capsys, tmp_path):
    _empty_blocklist(monkeypatch)
    cli.main(["record", "start", "--id", "conv-audit"])
    session_dir = capsys.readouterr().out.strip()
    cli.main(["record", "frame", "--session", session_dir, "--tool", "click"])
    cli.main(["record", "end", "--session", session_dir])
    capsys.readouterr()

    entries = [
        json.loads(line)
        for line in (tmp_path / "audit.log").read_text(encoding="utf-8").splitlines()
    ]
    actions = {e["action"] for e in entries}
    assert {"record_start", "record_frame", "record_end"} <= actions
    assert all(e["via"] == "record" for e in entries)
