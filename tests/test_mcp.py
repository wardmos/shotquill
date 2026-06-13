# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""MCP server tests: protocol framing, tool dispatch, and in-band errors.

The whole server is driven through ``serve()`` with StringIO pipes — exactly
the bytes an MCP client would exchange — so these tests cover the real
transport path, not just the handlers.
"""

from __future__ import annotations

import base64
import io
import json

import pytest

from shotquill import audit, headless, mcp, paths
from shotquill.capture.base import CaptureResult, DisplayInfo, Rect, WindowInfo

PNG_MAGIC = b"\x89PNG"


class FakeCapturer:
    def __init__(self, width: int = 2, height: int = 2) -> None:
        self.include_cursor = False
        self.size = (width, height)
        self.calls: list[tuple] = []
        self.windows = [
            WindowInfo(window_id=11, owner="Safari", title="GitHub", bounds=Rect(0, 25, 800, 600)),
            WindowInfo(window_id=22, owner="Safari", title="Docs", bounds=Rect(40, 25, 800, 600)),
            WindowInfo(window_id=33, owner="Notes", title="Scratch", bounds=Rect(5, 5, 300, 200)),
        ]
        self.displays = [
            DisplayInfo(
                index=0, name="built-in", bounds=Rect(0, 0, 1440, 900), scale=2.0, primary=True
            ),
            DisplayInfo(index=1, name="external", bounds=Rect(1440, -180, 1920, 1080)),
        ]

    def _result(self) -> CaptureResult:
        width, height = self.size
        return CaptureResult(
            width=width, height=height, scale=1.0, pixels=bytes([200] * width * height * 4)
        )

    def capture_fullscreen(self, exclude_window_ids=frozenset()) -> CaptureResult:
        self.calls.append(("fullscreen",))
        return self._result()

    def capture_region(self, region: Rect) -> CaptureResult:
        self.calls.append(("region", region))
        return self._result()

    def capture_window(self, window_id: int) -> CaptureResult:
        self.calls.append(("window", window_id))
        return self._result()

    def list_windows(self) -> list[WindowInfo]:
        return self.windows

    def list_displays(self) -> list[DisplayInfo]:
        return self.displays


@pytest.fixture(autouse=True)
def isolated_audit(monkeypatch, tmp_path):
    log = tmp_path / "audit.log"
    monkeypatch.setattr(paths, "audit_log_path", lambda: log)
    monkeypatch.setattr(audit, "_to_system_log", lambda line: None)
    monkeypatch.setattr(audit, "_caller_chain", lambda: ["pytest"])
    return log


@pytest.fixture
def fake_capturer(monkeypatch):
    pytest.importorskip("PySide6")
    capturer = FakeCapturer()
    monkeypatch.setattr(headless, "get_capturer", lambda include_cursor=False: capturer)
    return capturer


@pytest.fixture
def fake_recognizer(monkeypatch):
    class _Recognizer:
        def recognize(self, image):
            return ["hello", "world"]

    monkeypatch.setattr(headless, "get_recognizer", lambda: _Recognizer())


def run(*messages) -> list[dict]:
    """Feed JSON-RPC messages through serve() and return the responses."""
    raw = "\n".join(m if isinstance(m, str) else json.dumps(m) for m in messages) + "\n"
    fout = io.StringIO()
    assert mcp.serve(stdin=io.StringIO(raw), stdout=fout) == 0
    return [json.loads(line) for line in fout.getvalue().splitlines()]


def call(name: str, arguments: dict | None = None) -> dict:
    """One tools/call round-trip; returns the result object."""
    (response,) = run(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
    )
    assert response["id"] == 1
    return response


# --- protocol ----------------------------------------------------------------


def test_initialize_handshake():
    (response,) = run(
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {}},
        }
    )
    result = response["result"]
    assert result["protocolVersion"] == "2025-03-26"  # echo a supported version
    assert result["serverInfo"]["name"] == "shotquill"
    assert result["capabilities"]["tools"] == {}
    assert result["instructions"]


def test_initialize_unknown_version_offers_newest_supported():
    (response,) = run(
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "9999-12-31", "capabilities": {}},
        }
    )
    # Echoing an unknown version would claim conformance we cannot deliver;
    # the negotiation rules say to offer our newest instead.
    assert response["result"]["protocolVersion"] == mcp._SUPPORTED_PROTOCOLS[0]


def test_initialized_notification_gets_no_response():
    assert run({"jsonrpc": "2.0", "method": "notifications/initialized"}) == []


def test_ping():
    (response,) = run({"jsonrpc": "2.0", "id": 7, "method": "ping"})
    assert response == {"jsonrpc": "2.0", "id": 7, "result": {}}


def test_tools_list_descriptors():
    (response,) = run({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tools = {tool["name"]: tool for tool in response["result"]["tools"]}
    assert set(tools) == {
        "capture",
        "list_windows",
        "list_displays",
        "ocr",
        "doctor",
        "record_start",
        "record_frame",
        "record_end",
    }
    capture_schema = tools["capture"]["inputSchema"]
    assert "window_id" in capture_schema["properties"]
    assert "display" in capture_schema["properties"]
    assert capture_schema["additionalProperties"] is False
    assert "path" in tools["ocr"]["inputSchema"]["properties"]


def test_tools_list_annotations_and_output_schemas():
    (response,) = run({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tools = {tool["name"]: tool for tool in response["result"]["tools"]}
    # Screen-reading tools are flagged read-only so hosts can auto-approve
    # them; capture is not (save_path writes a file).
    for name in ("list_windows", "list_displays", "ocr", "doctor"):
        assert tools[name]["annotations"]["readOnlyHint"] is True
    assert "readOnlyHint" not in tools["capture"]["annotations"]
    for tool in tools.values():
        assert tool["annotations"]["title"]
        assert tool["annotations"]["openWorldHint"] is False
        assert tool["outputSchema"]["type"] == "object"


def test_unknown_method_is_error_but_unknown_notification_is_ignored():
    responses = run(
        {"jsonrpc": "2.0", "id": 1, "method": "resources/list"},
        {"jsonrpc": "2.0", "method": "notifications/whatever"},
    )
    (response,) = responses
    assert response["error"]["code"] == -32601


def test_parse_error():
    (response,) = run("this is not json {")
    assert response["error"]["code"] == -32700
    assert response["id"] is None


def test_unknown_tool_is_protocol_error():
    response = call("frobnicate")
    assert response["error"]["code"] == -32602


def test_non_object_params_is_invalid_params_and_keeps_serving():
    # One malformed message must answer with a JSON-RPC error, not kill the
    # session — the ping after it proves the server is still serving.
    responses = run(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": [1, 2, 3]},
        {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": "nope"},
        {"jsonrpc": "2.0", "id": 3, "method": "ping"},
    )
    assert responses[0]["error"]["code"] == -32602
    assert responses[1]["error"]["code"] == -32602
    assert responses[2] == {"jsonrpc": "2.0", "id": 3, "result": {}}


def test_non_object_params_notification_gets_no_response():
    # A notification never gets a response, not even an error one.
    responses = run(
        {"jsonrpc": "2.0", "method": "notifications/whatever", "params": [1]},
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
    )
    assert responses == [{"jsonrpc": "2.0", "id": 1, "result": {}}]


def test_non_object_arguments_is_invalid_params():
    (response,) = run(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "doctor", "arguments": "nope"},
        }
    )
    assert response["error"]["code"] == -32602


# --- capture tool ------------------------------------------------------------


def test_capture_returns_image_and_metadata(fake_capturer, isolated_audit):
    result = call("capture")["result"]
    assert result["isError"] is False
    image_item, text_item = result["content"]
    assert image_item["type"] == "image"
    assert image_item["mimeType"] == "image/png"
    assert base64.b64decode(image_item["data"]).startswith(PNG_MAGIC)
    meta = json.loads(text_item["text"])
    assert meta["target"] == "fullscreen"
    assert (meta["width"], meta["height"]) == (2, 2)
    assert result["structuredContent"] == meta  # typed mirror of the text block
    (entry,) = [
        json.loads(line) for line in isolated_audit.read_text(encoding="utf-8").splitlines()
    ]
    assert entry["via"] == "mcp"
    assert entry["dest"] == "inline"


def test_capture_app_reports_ambiguity(fake_capturer):
    result = call("capture", {"app": "safari"})["result"]
    meta = json.loads(result["content"][1]["text"])
    assert meta["target"] == "Safari — GitHub"
    assert meta["matched_windows"] == 2
    assert fake_capturer.calls == [("window", 11)]


def test_capture_save_path_also_writes_file(fake_capturer, tmp_path):
    dest = tmp_path / "out" / "shot.png"
    result = call("capture", {"save_path": str(dest)})["result"]
    meta = json.loads(result["content"][1]["text"])
    assert meta["saved_path"] == str(dest.resolve())
    with open(dest, "rb") as fh:
        assert fh.read(4) == PNG_MAGIC


def test_capture_max_width_downscales(fake_capturer):
    fake_capturer.size = (100, 40)
    result = call("capture", {"max_width": 50})["result"]
    meta = json.loads(result["content"][1]["text"])
    assert meta["width"] == 50
    assert meta["height"] == 20


@pytest.mark.parametrize("bad", [0, -10, True, 1.5, "50"])
def test_capture_invalid_max_width_is_invalid_arguments(fake_capturer, bad):
    # Mirror the CLI's positive-integer check; notably bool (an int subclass)
    # must not slip through as width 1 and silently emit a 1px image.
    result = call("capture", {"max_width": bad})["result"]
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["type"] == "invalid_arguments"


def test_capture_deterministic_routes_through_stable_encode(fake_capturer, monkeypatch):
    from shotquill import headless, mcp

    seen = {}

    def _record(image, fmt="png", *, deterministic=False):
        seen["deterministic"] = deterministic
        return b"\x89PNG\r\n\x1a\n"

    monkeypatch.setattr(mcp.headless, "encode_qimage", _record)
    call("capture", {"deterministic": True})
    assert seen["deterministic"] is True
    call("capture")
    assert seen["deterministic"] is False
    # signature stays compatible with the real encoder the tool calls
    assert "deterministic" in headless.encode_qimage.__kwdefaults__


def test_capture_conflicting_targets_is_invalid_arguments(fake_capturer):
    result = call("capture", {"window_id": 11, "app": "safari"})["result"]
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["type"] == "invalid_arguments"


def test_capture_unsupported_platform_is_in_band_error(monkeypatch):
    def _nope(include_cursor=False):
        raise headless.CapabilityUnsupported("capture", "no display session")

    monkeypatch.setattr(headless, "get_capturer", _nope)
    result = call("capture")["result"]
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["type"] == "unsupported"
    assert "no display session" in payload["error"]


def test_capture_no_window_match_is_no_match(fake_capturer):
    result = call("capture", {"app": "xcode"})["result"]
    payload = json.loads(result["content"][0]["text"])
    assert payload["type"] == "no_match"
    assert "list_windows" in payload["hint"]  # errors name the recovery step


def test_capture_blocked_app_is_in_band_blocked(fake_capturer, tmp_path):
    from shotquill import blocklist as bl

    bl.save(bl.Blocklist((bl.BlockRule(name="notes"),)), tmp_path / "blocklist.json")
    result = call("capture", {"app": "notes"})["result"]
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["type"] == "blocked"
    assert "do not retry" in payload["hint"]


# --- list_windows / ocr / doctor ----------------------------------------------


def test_list_windows_payload(fake_capturer):
    result = call("list_windows")["result"]
    payload = json.loads(result["content"][0]["text"])
    assert payload["windows"][0] == {
        "id": 11,
        "owner": "Safari",
        "title": "GitHub",
        "bundle_id": None,
        "bounds": {"x": 0, "y": 25, "width": 800, "height": 600},
    }
    assert result["structuredContent"] == payload


def test_list_displays_payload(fake_capturer):
    result = call("list_displays")["result"]
    payload = json.loads(result["content"][0]["text"])
    assert payload["displays"][0] == {
        "index": 0,
        "name": "built-in",
        "primary": True,
        "scale": 2.0,
        "bounds": {"x": 0, "y": 0, "width": 1440, "height": 900},
    }
    assert result["structuredContent"] == payload


def test_capture_display_crops_that_monitor(fake_capturer):
    result = call("capture", {"display": 1})["result"]
    assert result["isError"] is False
    meta = json.loads(result["content"][1]["text"])
    assert meta["target"] == "display 1 (1920x1080 at 1440,-180)"
    assert fake_capturer.calls == [("region", Rect(x=1440, y=-180, width=1920, height=1080))]


def test_capture_unknown_display_is_no_match(fake_capturer):
    result = call("capture", {"display": 9})["result"]
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["type"] == "no_match"
    assert "list_displays" in payload["hint"]


def test_capture_display_excludes_other_targets(fake_capturer):
    result = call("capture", {"display": 0, "region": {"x": 0, "y": 0, "width": 5, "height": 5}})[
        "result"
    ]
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["type"] == "invalid_arguments"
    assert fake_capturer.calls == []


def test_capture_display_must_be_an_integer(fake_capturer):
    result = call("capture", {"display": "0"})["result"]
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["type"] == "invalid_arguments"


def test_windows_payload_carries_bundle_id():
    from shotquill.capture.base import Rect, WindowInfo

    windows = [
        WindowInfo(
            11, "1Password", "Vault", Rect(0, 0, 400, 300), bundle_id="com.1password.1password"
        ),
    ]
    assert headless.windows_payload(windows)[0]["bundle_id"] == "com.1password.1password"


def test_ocr_from_file(fake_recognizer, fake_capturer, tmp_path):
    pytest.importorskip("PySide6")
    from shotquill.imaging import result_to_qimage

    image_file = tmp_path / "shot.png"
    image_file.write_bytes(headless.encode_qimage(result_to_qimage(FakeCapturer()._result())))
    result = call("ocr", {"path": str(image_file)})["result"]
    assert result["content"] == [{"type": "text", "text": "hello\nworld"}]
    assert result["structuredContent"]["lines"] == ["hello", "world"]


def test_ocr_capture_and_recognize_in_memory(fake_recognizer, fake_capturer, isolated_audit):
    result = call("ocr", {"app": "notes"})["result"]
    assert result["content"] == [{"type": "text", "text": "hello\nworld"}]
    assert result["structuredContent"]["source"] == "Notes — Scratch"
    assert fake_capturer.calls == [("window", 33)]
    (entry,) = [
        json.loads(line) for line in isolated_audit.read_text(encoding="utf-8").splitlines()
    ]
    assert entry["action"] == "ocr"
    assert entry["target"] == "Notes — Scratch"


def test_ocr_path_and_capture_target_is_invalid_arguments(fake_recognizer, fake_capturer):
    result = call("ocr", {"path": "/tmp/x.png", "app": "notes"})["result"]
    assert result["isError"] is True
    assert json.loads(result["content"][0]["text"])["type"] == "invalid_arguments"
    assert fake_capturer.calls == []  # neither interpretation was guessed at


def test_ocr_fails_fast_when_recognizer_unavailable(fake_capturer, monkeypatch):
    def _nope():
        raise headless.CapabilityUnsupported("ocr", "requires macOS Vision")

    monkeypatch.setattr(headless, "get_recognizer", _nope)
    result = call("ocr")["result"]
    assert result["isError"] is True
    assert json.loads(result["content"][0]["text"])["type"] == "unsupported"
    assert fake_capturer.calls == []  # no speculative screenshot before the check


def test_doctor_matrix(fake_capturer):
    result = call("doctor")["result"]
    payload = json.loads(result["content"][0]["text"])
    capabilities = {item["capability"] for item in payload["checks"]}
    assert {"platform", "capture", "list_windows", "ocr"} <= capabilities
    assert result["structuredContent"] == payload


# --- record_* ----------------------------------------------------------------


@pytest.fixture
def record_root(monkeypatch, tmp_path):
    """Point flight-recorder sessions at tmp and keep the blocklist empty."""
    from shotquill import blocklist as bl

    root = tmp_path / "records"
    monkeypatch.setattr(paths, "records_dir", lambda: root)
    monkeypatch.setattr(headless, "active_blocklist", lambda: bl.Blocklist(()))
    return root


def test_record_round_trip(fake_capturer, record_root):
    start = call("record_start", {"id": "conv-mcp", "agent": "builder"})["result"]
    sid = start["structuredContent"]["conversation_id"]
    assert sid == "conv-mcp"

    frame = call("record_frame", {"session": sid, "tool": "click", "label": "submit"})["result"]
    fdata = frame["structuredContent"]
    assert fdata["index"] == 1
    assert fdata["redacted"] is False  # empty blocklist
    # Absolute path with native separators (backslashes on Windows).
    assert fdata["image"].replace("\\", "/").endswith("frames/0001.png")
    with open(fdata["image"], "rb") as fh:
        assert fh.read(4) == PNG_MAGIC

    end = call("record_end", {"session": sid})["result"]
    edata = end["structuredContent"]
    assert edata["frames"] == 1
    assert edata["filmstrip"].endswith("index.html")
    assert edata["otlp"].endswith("trace.otlp.json")
    manifest = json.loads((record_root / "conv-mcp" / "manifest.json").read_text())
    assert manifest["status"] == "complete"
    otlp_doc = json.loads((record_root / "conv-mcp" / "trace.otlp.json").read_text())
    assert otlp_doc["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["name"].startswith(
        "invoke_agent"
    )


def test_record_frame_unknown_session_is_no_session(fake_capturer, record_root):
    result = call("record_frame", {"session": "ghost", "tool": "click"})["result"]
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["type"] == "no_session"
    assert "record_start" in payload["hint"]
    assert fake_capturer.calls == []  # no capture before the session check


def test_record_frame_requires_tool(fake_capturer, record_root):
    call("record_start", {"id": "conv-notool"})
    result = call("record_frame", {"session": "conv-notool"})["result"]
    assert result["isError"] is True
    assert json.loads(result["content"][0]["text"])["type"] == "invalid_arguments"


def test_record_frame_redacted_flag_tracks_blocklist(fake_capturer, monkeypatch, tmp_path):
    from shotquill import blocklist as bl

    monkeypatch.setattr(paths, "records_dir", lambda: tmp_path / "records")
    monkeypatch.setattr(
        headless, "active_blocklist", lambda: bl.Blocklist((bl.BlockRule(name="1Password"),))
    )
    call("record_start", {"id": "conv-r"})
    frame = call("record_frame", {"session": "conv-r", "tool": "click"})["result"]
    assert frame["structuredContent"]["redacted"] is True


def test_record_audits_via_record(fake_capturer, record_root, isolated_audit):
    call("record_start", {"id": "conv-a"})
    call("record_frame", {"session": "conv-a", "tool": "click"})
    call("record_end", {"session": "conv-a"})
    entries = [json.loads(line) for line in isolated_audit.read_text().splitlines()]
    actions = {e["action"] for e in entries}
    assert {"record_start", "record_frame", "record_end"} <= actions
    assert all(e["via"] == "record" for e in entries if e["action"].startswith("record_"))
