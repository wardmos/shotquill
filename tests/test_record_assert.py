# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Assertion-in-trace tests: a recorded frame can carry an OCR verdict.

This is the feature-one/feature-two confluence — a failed test becomes a frame
in the session (and an error span in the OTLP trace), replayable later. The
store/format layers are pure; the CLI/MCP sections drive fakes for the capture
and the recognizer.
"""

from __future__ import annotations

import io
import json

import pytest

from shotquill import audit, cli, headless, mcp, otlp, paths, record
from shotquill.capture.base import CaptureResult, DisplayInfo, Rect, WindowInfo

_FAKE_PNG = b"\x89PNG\r\n\x1a\nfake"
_PASS = [{"kind": "contains", "pattern": "Login", "passed": True}]
_FAIL = [
    {"kind": "contains", "pattern": "Login", "passed": True},
    {"kind": "contains", "pattern": "Logout", "passed": False},
]


# --- store: assertions on a frame -------------------------------------------


def test_record_frame_stores_passing_assertion(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-a")
    frame = record.record_frame(
        session, image_bytes=_FAKE_PNG, tool="assert", target="window 1", assertions=_PASS
    )
    assert frame.assertion_passed is True
    entry = record.load_manifest(session)["frames"][0]
    assert entry["assertion_passed"] is True
    assert entry["assertions"] == _PASS


def test_record_frame_failing_assertion_marks_frame(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-b")
    frame = record.record_frame(
        session, image_bytes=_FAKE_PNG, tool="assert", target="t", assertions=_FAIL
    )
    assert frame.assertion_passed is False  # any failing check fails the frame
    assert record.load_manifest(session)["frames"][0]["assertion_passed"] is False


def test_frame_without_assertions_has_no_verdict(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-c")
    frame = record.record_frame(session, image_bytes=_FAKE_PNG, tool="click", target="t")
    assert frame.assertion_passed is None
    entry = record.load_manifest(session)["frames"][0]
    assert "assertion_passed" not in entry and "assertions" not in entry


# --- store: PII findings on a frame -----------------------------------------


def test_record_frame_stores_pii_findings(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-pii-store")
    findings = [{"kind": "email", "count": 2}, {"kind": "credit_card", "count": 1}]
    record.record_frame(session, image_bytes=_FAKE_PNG, tool="verify", target="t", pii=findings)
    assert record.load_manifest(session)["frames"][0]["pii"] == findings


def test_frame_without_pii_has_no_pii_field(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-no-pii")
    record.record_frame(session, image_bytes=_FAKE_PNG, tool="click", target="t")
    assert "pii" not in record.load_manifest(session)["frames"][0]


# --- OTLP: failed assertion -> error span -----------------------------------


def _manifest_with(assertion_passed) -> dict:
    frame = {
        "span": {"tool_name": "assert", "tool_call_id": "conv/frame/1"},
        "at": "2026-06-13T10:00:00-04:00",
        "label": None,
        "image": "frames/0001.png",
        "target": "window 1",
        "redacted": False,
    }
    if assertion_passed is not None:
        frame["assertion_passed"] = assertion_passed
    return {
        "shotquill_manifest_version": 1,
        "conversation_id": "conv",
        "agent": {"name": None, "id": None},
        "status": "complete",
        "started_at": "2026-06-13T10:00:00-04:00",
        "ended_at": "2026-06-13T10:00:01-04:00",
        "frames": [frame],
    }


def _frame_span(document: dict) -> dict:
    return document["resourceSpans"][0]["scopeSpans"][0]["spans"][1]


def test_failed_assertion_sets_span_status_error():
    span = _frame_span(otlp.manifest_to_otlp(_manifest_with(False)))
    assert span["status"]["code"] == 2  # OTLP ERROR
    attrs = {a["key"]: a["value"] for a in span["attributes"]}
    assert attrs["shotquill.frame.assertion.passed"] == {"boolValue": False}


def test_passed_assertion_leaves_status_unset():
    span = _frame_span(otlp.manifest_to_otlp(_manifest_with(True)))
    assert "status" not in span  # UNSET is the default; only failures flip it
    attrs = {a["key"]: a["value"] for a in span["attributes"]}
    assert attrs["shotquill.frame.assertion.passed"] == {"boolValue": True}


def test_no_assertion_no_status_no_attr():
    span = _frame_span(otlp.manifest_to_otlp(_manifest_with(None)))
    assert "status" not in span
    assert all(a["key"] != "shotquill.frame.assertion.passed" for a in span["attributes"])


# --- filmstrip badges -------------------------------------------------------


def test_filmstrip_marks_failed_assertion():
    html = record.render_filmstrip(_manifest_with(False))
    assert "assert FAIL" in html
    assert "frame failed" in html  # the failing card is outlined


def test_filmstrip_marks_passed_assertion():
    html = record.render_filmstrip(_manifest_with(True))
    assert "assert ok" in html
    assert "frame failed" not in html


# --- CLI / MCP integration --------------------------------------------------


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


class _Recognizer:
    def __init__(self, lines):
        self._lines = lines

    def recognize(self, image):
        return list(self._lines)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    from shotquill import blocklist as bl

    monkeypatch.setattr(paths, "records_dir", lambda: tmp_path / "records")
    monkeypatch.setattr(paths, "audit_log_path", lambda: tmp_path / "audit.log")
    monkeypatch.setattr(audit, "_to_system_log", lambda line: None)
    monkeypatch.setattr(audit, "_caller_chain", lambda: ["pytest"])
    monkeypatch.setattr(headless, "active_blocklist", lambda: bl.Blocklist(()))


@pytest.fixture
def fakes(monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setattr(headless, "get_capturer", lambda include_cursor=False: FakeCapturer())
    monkeypatch.setattr(headless, "get_recognizer", lambda: _Recognizer(["Login", "Welcome"]))


def test_cli_record_frame_assert_pass(fakes, capsys):
    cli.main(["session", "start", "--id", "conv-cli"])
    capsys.readouterr()
    rc = cli.main(["session", "frame", "conv-cli", "--tool", "assert", "--contains", "Login"])
    assert rc == 0
    assert "ok: text contains 'Login'" in capsys.readouterr().err


def test_cli_record_frame_assert_fail_still_records(fakes, capsys, tmp_path):
    cli.main(["session", "start", "--id", "conv-fail"])
    capsys.readouterr()
    rc = cli.main(["session", "frame", "conv-fail", "--tool", "assert", "--contains", "Logout"])
    assert rc == cli._EXIT_ASSERTION_FAILED  # 20: result, not error
    # The failing frame is recorded anyway — capturing the failure is the point.
    entry = json.loads((tmp_path / "records" / "conv-fail" / "manifest.json").read_text())[
        "frames"
    ][0]
    assert entry["assertion_passed"] is False


def test_cli_record_frame_invalid_regex_is_usage(fakes, capsys):
    cli.main(["session", "start", "--id", "conv-re"])
    capsys.readouterr()
    assert cli.main(["session", "frame", "conv-re", "--tool", "a", "--matches", "("]) == 2


def test_mcp_record_frame_assertion(fakes):
    def call(args):
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "session_frame", "arguments": args},
            }
        )
        fout = io.StringIO()
        mcp.serve(stdin=io.StringIO(raw + "\n"), stdout=fout)
        return json.loads(fout.getvalue())["result"]

    start = io.StringIO()
    mcp.serve(
        stdin=io.StringIO(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "session_start", "arguments": {"id": "conv-mcp"}},
                }
            )
            + "\n"
        ),
        stdout=start,
    )
    structured = call({"session": "conv-mcp", "tool": "assert", "contains": ["Login"]})[
        "structuredContent"
    ]
    assert structured["assertion_passed"] is True
    assert structured["assertions"][0]["pattern"] == "Login"


def test_cli_record_frame_scan_pii(fakes, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        headless,
        "get_recognizer",
        lambda: _Recognizer(["email ada@example.com", "card 4111111111111111"]),
    )
    cli.main(["session", "start", "--id", "conv-pii"])
    capsys.readouterr()
    rc = cli.main(["session", "frame", "conv-pii", "--tool", "verify", "--scan-pii"])
    assert rc == 0
    assert "pii scan: likely" in capsys.readouterr().err

    entry = json.loads((tmp_path / "records" / "conv-pii" / "manifest.json").read_text())["frames"][
        0
    ]
    assert {f["kind"] for f in entry["pii"]} == {"email", "credit_card"}
    # The matched values are never written to the manifest — kind + count only.
    assert "ada@example.com" not in json.dumps(entry)
    assert "4111111111111111" not in json.dumps(entry)


def test_mcp_record_frame_scan_pii(fakes, monkeypatch):
    monkeypatch.setattr(headless, "get_recognizer", lambda: _Recognizer(["ada@example.com"]))

    def call(args):
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "session_frame", "arguments": args},
            }
        )
        fout = io.StringIO()
        mcp.serve(stdin=io.StringIO(raw + "\n"), stdout=fout)
        return json.loads(fout.getvalue())["result"]

    mcp.serve(
        stdin=io.StringIO(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "session_start", "arguments": {"id": "conv-mcp-pii"}},
                }
            )
            + "\n"
        ),
        stdout=io.StringIO(),
    )
    structured = call({"session": "conv-mcp-pii", "tool": "verify", "scan_pii": True})[
        "structuredContent"
    ]
    assert structured["pii"] == [{"kind": "email", "count": 1}]


# --- redact: mask the PII pixels before filing the frame ---------------------


class _BoxRecognizer:
    """OCR fake that returns a fixed PII box at (0,0,1,1), masking that corner."""

    def recognize_boxes(self, image):
        from shotquill.ocr.base import TextBox

        return [TextBox("ada@example.com", 0, 0, 1, 1)]

    def recognize(self, image):
        return ["ada@example.com"]


def test_cli_record_frame_redact_pii_masks_filed_frame(fakes, monkeypatch, capsys):
    from PySide6.QtGui import QImage

    monkeypatch.setattr(headless, "get_recognizer", lambda: _BoxRecognizer())
    cli.main(["session", "start", "--id", "conv-redact"])
    capsys.readouterr()
    rc = cli.main(["session", "frame", "conv-redact", "--tool", "verify", "--redact-pii"])
    assert rc == 0
    dest = capsys.readouterr().out.strip()
    img = QImage(dest)
    assert img.pixelColor(0, 0).getRgb()[:3] == (0, 0, 0)  # PII box → masked
    assert img.pixelColor(1, 1).getRgb()[:3] == (255, 0, 0)  # rest of frame intact


def test_mcp_record_frame_redact_pii_masks_filed_frame(fakes, monkeypatch, tmp_path):
    import json as _json

    from PySide6.QtGui import QImage

    monkeypatch.setattr(headless, "get_recognizer", lambda: _BoxRecognizer())

    def serve_call(name, arguments):
        raw = _json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        fout = io.StringIO()
        mcp.serve(stdin=io.StringIO(raw + "\n"), stdout=fout)
        return _json.loads(fout.getvalue())["result"]

    serve_call("session_start", {"id": "conv-mcp-redact"})
    serve_call(
        "session_frame", {"session": "conv-mcp-redact", "tool": "verify", "redact_pii": True}
    )

    session_dir = tmp_path / "records" / "conv-mcp-redact"
    manifest = _json.loads((session_dir / "manifest.json").read_text())
    img = QImage(str(session_dir / manifest["frames"][0]["image"]))
    assert img.pixelColor(0, 0).getRgb()[:3] == (0, 0, 0)
    assert img.pixelColor(1, 1).getRgb()[:3] == (255, 0, 0)


# --- before/after pairing over MCP -------------------------------------------


def test_mcp_record_frame_before_after_pairs(fakes):
    import json as _json

    def serve_call(name, arguments):
        raw = _json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        fout = io.StringIO()
        mcp.serve(stdin=io.StringIO(raw + "\n"), stdout=fout)
        return _json.loads(fout.getvalue())["result"]["structuredContent"]

    serve_call("session_start", {"id": "conv-mcp-ba"})
    before = serve_call(
        "session_frame", {"session": "conv-mcp-ba", "tool": "click", "phase": "before"}
    )
    after = serve_call(
        "session_frame", {"session": "conv-mcp-ba", "tool": "click", "phase": "after"}
    )
    assert before["phase"] == "before" and after["phase"] == "after"
    assert before["pair_id"] == after["pair_id"]
