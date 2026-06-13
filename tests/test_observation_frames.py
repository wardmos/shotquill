# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Observation-frame tests (D3): a passive capture mirrored into the trace.

While a session is recording, a `capture` the agent did to *see* the screen
also lands as an `observation` frame — distinct from a deliberate `action`
frame, so a glance never masquerades as a step. The store/format layers are
pure; the MCP section drives the active-session mirroring, the CLI section the
explicit `capture --session`.
"""

from __future__ import annotations

import io
import json

import pytest

from shotquill import audit, cli, headless, mcp, otlp, paths, record
from shotquill.capture.base import CaptureResult, DisplayInfo, Rect, WindowInfo

# --- store + format ---------------------------------------------------------


def test_record_frame_kind_defaults_action(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="c1")
    a = record.record_frame(session, image_bytes=b"x", tool="click", target="t")
    o = record.record_frame(
        session, image_bytes=b"x", tool="observe", target="t", kind=record.KIND_OBSERVATION
    )
    frames = record.load_manifest(session)["frames"]
    assert a.kind == "action" and frames[0]["kind"] == "action"
    assert o.kind == "observation" and frames[1]["kind"] == "observation"


def _manifest(frames: list[dict]) -> dict:
    return {
        "shotquill_manifest_version": 1,
        "conversation_id": "conv",
        "agent": {"name": None, "id": None},
        "status": "complete",
        "started_at": "2026-06-13T10:00:00-04:00",
        "ended_at": "2026-06-13T10:00:05-04:00",
        "frames": frames,
    }


def _frame(kind: str, tool: str) -> dict:
    return {
        "span": {"tool_name": tool, "tool_call_id": f"conv/frame/{tool}"},
        "at": "2026-06-13T10:00:02-04:00",
        "kind": kind,
        "label": None,
        "image": f"frames/{tool}.png",
        "target": "window 1",
        "redacted": False,
    }


def test_observation_frame_is_root_event_not_child_span():
    doc = otlp.manifest_to_otlp(
        _manifest([_frame("action", "click"), _frame("observation", "observe")])
    )
    spans = doc["resourceSpans"][0]["scopeSpans"][0]["spans"]
    # Root span + exactly one child span (the action). The observation does NOT
    # become an execute_tool span.
    assert len(spans) == 2
    root, action = spans
    assert action["name"] == "execute_tool click"
    # The observation rides as an event on the root agent span.
    event_names = [e["name"] for e in root.get("events", [])]
    assert event_names == ["shotquill.frame"]
    obs_attrs = {a["key"]: a["value"] for a in root["events"][0]["attributes"]}
    assert obs_attrs["shotquill.frame.kind"] == {"stringValue": "observation"}


def test_all_observations_leave_no_child_spans():
    doc = otlp.manifest_to_otlp(_manifest([_frame("observation", "a"), _frame("observation", "b")]))
    spans = doc["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert len(spans) == 1  # just the root
    assert len(spans[0]["events"]) == 2


def test_filmstrip_marks_observation():
    html = record.render_filmstrip(_manifest([_frame("observation", "observe")]))
    assert "observation" in html
    assert "frame observation" in html  # dimmed card class


# --- MCP: active session mirrors captures -----------------------------------


def _result() -> CaptureResult:
    return CaptureResult(width=2, height=2, scale=1.0, pixels=bytes([255, 0, 0, 255] * 4))


class FakeCapturer:
    include_cursor = False
    windows = [WindowInfo(window_id=1, owner="App", title="T", bounds=Rect(0, 0, 100, 100))]
    displays = [DisplayInfo(index=0, name="d", bounds=Rect(0, 0, 100, 100), primary=True)]

    def capture_fullscreen(self, exclude_window_ids=frozenset()):
        return _result()

    def capture_region(self, region):
        return _result()

    def capture_window(self, window_id):
        return _result()

    def list_windows(self):
        return self.windows

    def list_displays(self):
        return self.displays


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    from shotquill import blocklist as bl

    monkeypatch.setattr(paths, "records_dir", lambda: tmp_path / "records")
    monkeypatch.setattr(paths, "audit_log_path", lambda: tmp_path / "audit.log")
    monkeypatch.setattr(audit, "_to_system_log", lambda line: None)
    monkeypatch.setattr(audit, "_caller_chain", lambda: ["pytest"])
    monkeypatch.setattr(headless, "active_blocklist", lambda: bl.Blocklist(()))


@pytest.fixture
def fake_capturer(monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setattr(headless, "get_capturer", lambda include_cursor=False: FakeCapturer())


def _msg(msg_id: int, name: str, args: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": args},
    }


def _run(*messages: dict) -> dict[int, dict]:
    """Drive one stdio connection (one serve() call) and index results by id.

    The active recording session is per-connection, so start/capture/end must
    share a single serve() — exactly how a real MCP client talks to the server.
    """
    raw = "\n".join(json.dumps(m) for m in messages) + "\n"
    fout = io.StringIO()
    mcp.serve(stdin=io.StringIO(raw), stdout=fout)
    return {
        r["id"]: r["result"] for r in (json.loads(line) for line in fout.getvalue().splitlines())
    }


def test_capture_mirrors_into_active_session(fake_capturer, tmp_path):
    out = _run(_msg(1, "record_start", {"id": "conv-mcp"}), _msg(2, "capture", {}))
    cap = out[2]["structuredContent"]
    assert cap["recorded"]["index"] == 1
    assert cap["recorded"]["conversation_id"] == "conv-mcp"
    # The mirrored frame is an observation in the manifest.
    manifest = json.loads((tmp_path / "records" / "conv-mcp" / "manifest.json").read_text())
    assert manifest["frames"][0]["kind"] == "observation"


def test_capture_record_false_skips_mirror(fake_capturer):
    out = _run(_msg(1, "record_start", {"id": "conv-skip"}), _msg(2, "capture", {"record": False}))
    assert "recorded" not in out[2]["structuredContent"]


def test_capture_without_active_session_is_unchanged(fake_capturer):
    out = _run(_msg(1, "capture", {}))
    assert "recorded" not in out[1]["structuredContent"]


def test_record_end_stops_mirroring(fake_capturer):
    out = _run(
        _msg(1, "record_start", {"id": "conv-end"}),
        _msg(2, "record_end", {"session": "conv-end"}),
        _msg(3, "capture", {}),
    )
    assert "recorded" not in out[3]["structuredContent"]  # no longer recording


# --- CLI: explicit capture --session ----------------------------------------


def test_cli_capture_session_files_observation(fake_capturer, capsys, tmp_path):
    cli.main(["record", "start", "--id", "conv-cli"])
    capsys.readouterr()
    rc = cli.main(["capture", "--session", "conv-cli", "-o", str(tmp_path / "shot.png")])
    assert rc == 0
    manifest = json.loads((tmp_path / "records" / "conv-cli" / "manifest.json").read_text())
    assert manifest["frames"][0]["kind"] == "observation"


def test_cli_capture_bad_session_is_error(fake_capturer, capsys, tmp_path):
    rc = cli.main(["capture", "--session", "ghost", "-o", str(tmp_path / "s.png")])
    assert rc == 1
    assert "no recording session" in capsys.readouterr().err
