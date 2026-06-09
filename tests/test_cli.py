# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""CLI contract tests: dispatch, output forms, exit codes, audit trail.

Everything runs against an in-memory FakeCapturer — the platform backends are
exercised by their own tests (and, for qtgrab, by test_qtgrab.py under the
offscreen platform).
"""

from __future__ import annotations

import io
import json
import sys
import types

import pytest

from shotquill import audit, cli, headless, paths
from shotquill.capture.base import CaptureResult, Rect, WindowInfo

PNG_MAGIC = b"\x89PNG"


def _result(width: int = 2, height: int = 2) -> CaptureResult:
    return CaptureResult(
        width=width, height=height, scale=1.0, pixels=bytes([255, 0, 0, 255] * width * height)
    )


class FakeCapturer:
    def __init__(self) -> None:
        self.include_cursor = False
        self.calls: list[tuple] = []
        self.windows = [
            WindowInfo(window_id=11, owner="Safari", title="GitHub", bounds=Rect(0, 25, 800, 600)),
            WindowInfo(window_id=22, owner="Safari", title="Docs", bounds=Rect(40, 25, 800, 600)),
            WindowInfo(window_id=33, owner="Notes", title="Scratch", bounds=Rect(5, 5, 300, 200)),
        ]

    def capture_fullscreen(self, exclude_window_ids=frozenset()) -> CaptureResult:
        self.calls.append(("fullscreen",))
        return _result()

    def capture_region(self, region: Rect) -> CaptureResult:
        self.calls.append(("region", region))
        return _result()

    def capture_window(self, window_id: int) -> CaptureResult:
        self.calls.append(("window", window_id))
        return _result()

    def list_windows(self) -> list[WindowInfo]:
        return self.windows


@pytest.fixture(autouse=True)
def isolated_audit(monkeypatch, tmp_path):
    """Keep audit writes inside tmp and away from syslog / process walking."""
    log = tmp_path / "audit.log"
    monkeypatch.setattr(paths, "audit_log_path", lambda: log)
    monkeypatch.setattr(paths, "capture_tmp_dir", lambda: tmp_path / "captures")
    monkeypatch.setattr(audit, "_to_system_log", lambda line: None)
    monkeypatch.setattr(audit, "_caller_chain", lambda: ["pytest"])
    return log


@pytest.fixture
def fake_capturer(monkeypatch):
    pytest.importorskip("PySide6")
    capturer = FakeCapturer()
    monkeypatch.setattr(headless, "get_capturer", lambda include_cursor=False: capturer)
    return capturer


def _audit_entries(log) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


# --- dispatch ---------------------------------------------------------------


def test_no_args_launches_gui(monkeypatch):
    pytest.importorskip("PySide6")
    from shotquill import app as app_module

    monkeypatch.setattr(app_module, "run", lambda: 42)
    assert cli.main([]) == 42


def test_version_flag_exits_zero():
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0


def test_unknown_command_is_usage_error():
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["frobnicate"])
    assert excinfo.value.code == 2


@pytest.mark.parametrize("argv", [["--help"], ["capture", "--help"], ["ocr", "--help"]])
def test_help_documents_exit_codes(capsys, argv):
    # Agents discover the exit-code contract from --help, not the README.
    with pytest.raises(SystemExit) as excinfo:
        cli.main(argv)
    assert excinfo.value.code == 0
    assert "exit codes:" in capsys.readouterr().out


# --- capture: targets -------------------------------------------------------


def test_capture_default_is_fullscreen_and_prints_path(fake_capturer, capsys, isolated_audit):
    assert cli.main(["capture"]) == 0
    out = capsys.readouterr().out.strip()
    assert out  # exactly one absolute path on stdout
    assert "\n" not in out
    assert out.startswith("/")
    with open(out, "rb") as fh:
        assert fh.read(4) == PNG_MAGIC
    assert fake_capturer.calls == [("fullscreen",)]


def test_capture_window_id(fake_capturer, capsys):
    assert cli.main(["capture", "--window-id", "33"]) == 0
    assert fake_capturer.calls == [("window", 33)]


def test_capture_app_picks_front_most_and_warns(fake_capturer, capsys):
    assert cli.main(["capture", "--app", "safari"]) == 0
    captured = capsys.readouterr()
    assert fake_capturer.calls == [("window", 11)]  # front-most match
    assert "2 windows match" in captured.err
    assert "--window-id" in captured.err


def test_capture_app_title_narrows_without_warning(fake_capturer, capsys):
    assert cli.main(["capture", "--app", "safari", "--title", "docs"]) == 0
    captured = capsys.readouterr()
    assert fake_capturer.calls == [("window", 22)]
    assert "match" not in captured.err


def test_capture_app_no_match_exits_5(fake_capturer, capsys):
    assert cli.main(["capture", "--app", "xcode"]) == headless.EXIT_NO_MATCH
    assert "no on-screen window" in capsys.readouterr().err


def test_capture_region(fake_capturer):
    assert cli.main(["capture", "--region", "10,20,30,40"]) == 0
    assert fake_capturer.calls == [("region", Rect(x=10, y=20, width=30, height=40))]


@pytest.mark.parametrize("bad", ["10,20", "a,b,c,d", "0,0,-1,5", "1,2,3,4,5"])
def test_capture_bad_region_is_usage_error(fake_capturer, capsys, bad):
    assert cli.main(["capture", "--region", bad]) == 2
    assert capsys.readouterr().err


def test_capture_title_without_app_is_usage_error(fake_capturer, capsys):
    assert cli.main(["capture", "--title", "x"]) == 2
    assert "--app" in capsys.readouterr().err


def test_capture_targets_are_mutually_exclusive(fake_capturer):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["capture", "--window-id", "1", "--app", "safari"])
    assert excinfo.value.code == 2


# --- capture: output forms --------------------------------------------------


def test_capture_explicit_output_path(fake_capturer, capsys, tmp_path):
    dest = tmp_path / "nested" / "shot.jpg"
    assert cli.main(["capture", "-o", str(dest)]) == 0
    assert dest.exists()
    assert capsys.readouterr().out.strip() == str(dest.resolve())


def test_capture_stdout_streams_png(fake_capturer, capsysbinary):
    assert cli.main(["capture", "-o", "-"]) == 0
    out = capsysbinary.readouterr().out
    assert out.startswith(PNG_MAGIC)


def test_capture_stdout_refused_on_tty(fake_capturer, capsys, monkeypatch):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert cli.main(["capture", "-o", "-"]) == 2
    assert "terminal" in capsys.readouterr().err


def test_capture_json_metadata(fake_capturer, capsys, isolated_audit):
    assert cli.main(["capture", "--json", "--app", "safari"]) == 0
    captured = capsys.readouterr()
    meta = json.loads(captured.out)
    assert meta["target"] == "Safari — GitHub"
    assert (meta["width"], meta["height"]) == (2, 2)
    assert meta["matched_windows"] == 2  # ambiguity rides in-band …
    assert "match" not in captured.err  # … not as stderr prose
    with open(meta["path"], "rb") as fh:
        assert fh.read(4) == PNG_MAGIC


def test_capture_json_refused_with_stdout_stream(fake_capturer, capsys):
    assert cli.main(["capture", "--json", "-o", "-"]) == 2
    assert "--json" in capsys.readouterr().err


def test_capture_max_width_downscales(fake_capturer, capsys, monkeypatch):
    monkeypatch.setattr(fake_capturer, "capture_fullscreen", lambda: _result(100, 40))
    assert cli.main(["capture", "--json", "--max-width", "50"]) == 0
    meta = json.loads(capsys.readouterr().out)
    assert (meta["width"], meta["height"]) == (50, 20)


def test_capture_max_width_must_be_positive(fake_capturer, capsys):
    assert cli.main(["capture", "--max-width", "0"]) == 2
    assert fake_capturer.calls == []


# --- capture: failures ------------------------------------------------------


def test_capture_permission_error_exits_3(fake_capturer, capsys, monkeypatch):
    def _denied():
        raise PermissionError("screen recording not granted")

    monkeypatch.setattr(fake_capturer, "capture_fullscreen", _denied)
    assert cli.main(["capture"]) == headless.EXIT_PERMISSION
    assert "permission" in capsys.readouterr().err.lower()


def test_capture_unsupported_exits_4(monkeypatch, capsys):
    def _no_backend(include_cursor=False):
        raise headless.CapabilityUnsupported("capture", "no display session")

    monkeypatch.setattr(headless, "get_capturer", _no_backend)
    assert cli.main(["capture"]) == headless.EXIT_UNSUPPORTED
    assert "no display session" in capsys.readouterr().err


def test_capture_empty_app_is_usage_error_not_fullscreen(fake_capturer, capsys):
    assert cli.main(["capture", "--app", ""]) == 2
    assert fake_capturer.calls == []  # crucially: no silent full-screen grab
    assert "non-empty" in capsys.readouterr().err


def test_unexpected_backend_error_is_clean_exit_1(fake_capturer, capsys, monkeypatch):
    def _boom():
        raise RuntimeError("ScreenCaptureKit gave up")

    monkeypatch.setattr(fake_capturer, "capture_fullscreen", _boom)
    assert cli.main(["capture"]) == 1
    err = capsys.readouterr().err
    assert "ScreenCaptureKit gave up" in err
    assert "Traceback" not in err


def test_broken_pipe_is_quiet_exit_1(fake_capturer, capsys, monkeypatch):
    def _downstream_closed():
        raise BrokenPipeError

    monkeypatch.setattr(fake_capturer, "capture_fullscreen", _downstream_closed)
    assert cli.main(["capture"]) == 1
    assert "Traceback" not in capsys.readouterr().err


def test_unknown_extension_warns_on_stderr(fake_capturer, capsys, tmp_path):
    dest = tmp_path / "shot.webp"
    assert cli.main(["capture", "-o", str(dest)]) == 0
    captured = capsys.readouterr()
    assert "unknown extension .webp" in captured.err
    assert captured.out.strip() == str(dest.resolve())  # stdout contract intact
    with open(dest, "rb") as fh:
        assert fh.read(4) == PNG_MAGIC


# --- audit ------------------------------------------------------------------


def test_capture_writes_audit_entry(fake_capturer, capsys, isolated_audit):
    assert cli.main(["capture", "--app", "notes"]) == 0
    (entry,) = _audit_entries(isolated_audit)
    assert entry["via"] == "cli"
    assert entry["action"] == "capture"
    assert entry["target"] == "Notes — Scratch"  # the window actually hit, not the request
    assert entry["dest"].endswith(".png")
    assert entry["caller"] == ["pytest"]


def test_audit_failure_does_not_break_capture(fake_capturer, capsys, monkeypatch):
    def _boom():
        raise OSError("read-only filesystem")

    monkeypatch.setattr(paths, "audit_log_path", _boom)
    assert cli.main(["capture"]) == 0  # capture still succeeds, path printed
    assert capsys.readouterr().out.strip()


# --- windows ----------------------------------------------------------------


def test_windows_table(fake_capturer, capsys):
    assert cli.main(["windows"]) == 0
    out = capsys.readouterr().out
    assert "OWNER" in out and "TITLE" in out
    assert "Safari" in out and "GitHub" in out


def test_capture_blocked_app_exits_6(fake_capturer, tmp_path, capsys):
    from shotquill import blocklist as bl

    bl.save(bl.Blocklist((bl.BlockRule(name="notes"),)), tmp_path / "blocklist.json")
    assert cli.main(["capture", "--app", "notes"]) == headless.EXIT_BLOCKED
    assert "blocklist" in capsys.readouterr().err.lower()
    assert fake_capturer.calls == []  # refused before any capture


def test_doctor_lists_blocklist(fake_capturer, tmp_path, capsys):
    from shotquill import blocklist as bl

    bl.save(bl.Blocklist((bl.BlockRule(name="notes"),)), tmp_path / "blocklist.json")
    assert cli.main(["doctor"]) == 0
    assert "app_blocklist" in capsys.readouterr().out


# --- blocklist management ---------------------------------------------------


def _load_blocklist(tmp_path):
    from shotquill import blocklist as bl

    return bl.load(tmp_path / "blocklist.json")


def test_blocklist_add_then_list(tmp_path, capsys):
    assert cli.main(["blocklist", "add", "--bundle-id", "com.1password.1password"]) == 0
    assert capsys.readouterr().out.strip() == "com.1password.1password"
    assert cli.main(["blocklist", "add", "--name", "keychain"]) == 0
    capsys.readouterr()
    assert cli.main(["blocklist", "list"]) == 0
    out = capsys.readouterr().out
    assert "com.1password.1password" in out and "name~keychain" in out


def test_blocklist_add_is_idempotent(tmp_path, capsys):
    cli.main(["blocklist", "add", "--name", "keychain"])
    capsys.readouterr()
    assert cli.main(["blocklist", "add", "--name", "keychain"]) == 0
    assert "already" in capsys.readouterr().err
    rules = _load_blocklist(tmp_path).rules
    assert len(rules) == 1


def test_blocklist_remove(tmp_path, capsys):
    cli.main(["blocklist", "add", "--bundle-id", "com.apple.keychainaccess"])
    capsys.readouterr()
    assert cli.main(["blocklist", "remove", "--bundle-id", "com.apple.keychainaccess"]) == 0
    assert _load_blocklist(tmp_path).rules == ()


def test_blocklist_remove_absent_is_noop_with_note(tmp_path, capsys):
    assert cli.main(["blocklist", "remove", "--name", "ghost"]) == 0
    assert "was not on the blocklist" in capsys.readouterr().err


def test_blocklist_list_empty(tmp_path, capsys):
    assert cli.main(["blocklist", "list"]) == 0
    assert capsys.readouterr().out.strip() == "(empty)"


def test_blocklist_list_json(tmp_path, capsys):
    cli.main(["blocklist", "add", "--bundle-id", "com.x"])
    capsys.readouterr()
    assert cli.main(["blocklist", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == [{"bundle_id": "com.x"}]


def test_blocklist_add_requires_a_selector(tmp_path, capsys):
    # The mutually exclusive group is required: neither flag is a usage error.
    with pytest.raises(SystemExit) as exc:
        cli.main(["blocklist", "add"])
    assert exc.value.code == 2


def test_windows_json(fake_capturer, capsys):
    assert cli.main(["windows", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0] == {
        "id": 11,
        "owner": "Safari",
        "title": "GitHub",
        "bundle_id": None,
        "bounds": {"x": 0, "y": 25, "width": 800, "height": 600},
    }


def test_windows_unsupported_exits_4(fake_capturer, capsys, monkeypatch):
    def _nope():
        raise headless.CapabilityUnsupported("list_windows", "wayland")

    monkeypatch.setattr(fake_capturer, "list_windows", _nope)
    assert cli.main(["windows"]) == headless.EXIT_UNSUPPORTED


# --- ocr --------------------------------------------------------------------


def _png_bytes() -> bytes:
    from shotquill.imaging import result_to_qimage

    return headless.encode_qimage(result_to_qimage(_result()), "png")


@pytest.fixture
def fake_recognizer(monkeypatch):
    pytest.importorskip("PySide6")

    class _Recognizer:
        def recognize(self, image):
            return ["hello", "world"]

    monkeypatch.setattr(headless, "get_recognizer", lambda: _Recognizer())


def test_ocr_file(fake_recognizer, capsys, tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(_png_bytes())
    assert cli.main(["ocr", str(image)]) == 0
    assert capsys.readouterr().out == "hello\nworld\n"


def test_ocr_stdin(fake_recognizer, capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(buffer=io.BytesIO(_png_bytes())))
    assert cli.main(["ocr", "-"]) == 0
    assert capsys.readouterr().out == "hello\nworld\n"


def test_ocr_undecodable_input_fails(fake_recognizer, capsys, tmp_path):
    bogus = tmp_path / "not-an-image.png"
    bogus.write_bytes(b"definitely not pixels")
    assert cli.main(["ocr", str(bogus)]) == 1
    assert "not a decodable image" in capsys.readouterr().err


def test_ocr_missing_file_fails(fake_recognizer, capsys, tmp_path):
    assert cli.main(["ocr", str(tmp_path / "absent.png")]) == 1


def test_ocr_unsupported_exits_4(monkeypatch, capsys):
    def _nope():
        raise headless.CapabilityUnsupported("ocr", "requires macOS Vision")

    monkeypatch.setattr(headless, "get_recognizer", _nope)
    assert cli.main(["ocr", "whatever.png"]) == headless.EXIT_UNSUPPORTED


def test_ocr_captures_when_no_path(fake_recognizer, fake_capturer, capsys, isolated_audit):
    # One step instead of `capture -o - | squill ocr -`, like the MCP tool.
    assert cli.main(["ocr", "--app", "notes"]) == 0
    assert capsys.readouterr().out == "hello\nworld\n"
    assert fake_capturer.calls == [("window", 33)]
    (entry,) = _audit_entries(isolated_audit)
    assert entry["action"] == "ocr"
    assert entry["target"] == "Notes — Scratch"


def test_ocr_bare_invocation_recognizes_fullscreen(fake_recognizer, fake_capturer, capsys):
    assert cli.main(["ocr"]) == 0
    assert capsys.readouterr().out == "hello\nworld\n"
    assert fake_capturer.calls == [("fullscreen",)]


def test_ocr_path_and_target_is_usage_error(fake_recognizer, fake_capturer, capsys):
    assert cli.main(["ocr", "shot.png", "--app", "notes"]) == 2
    assert fake_capturer.calls == []  # neither interpretation was guessed at
    assert "not both" in capsys.readouterr().err


def test_ocr_path_and_title_is_usage_error(fake_recognizer, fake_capturer, capsys):
    # --title is a capture target too: silently OCRing the file while ignoring
    # it would answer a different question than the caller asked.
    assert cli.main(["ocr", "shot.png", "--title", "docs"]) == 2
    assert fake_capturer.calls == []
    assert "not both" in capsys.readouterr().err


def test_ocr_title_without_app_is_usage_error(fake_recognizer, fake_capturer, capsys):
    assert cli.main(["ocr", "--title", "docs"]) == 2
    assert "--app" in capsys.readouterr().err


def test_ocr_usage_error_wins_over_unavailable_recognizer(monkeypatch, capsys):
    # Exit codes are the contract agents branch on: a bad invocation is exit 2
    # even on a host where OCR itself would be unavailable (exit 4).
    def _nope():
        raise headless.CapabilityUnsupported("ocr", "requires macOS Vision")

    monkeypatch.setattr(headless, "get_recognizer", _nope)
    assert cli.main(["ocr", "shot.png", "--app", "notes"]) == 2


# --- doctor -----------------------------------------------------------------


def test_doctor_reports_capability_matrix(capsys, qapp):
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "platform" in out
    assert "capture" in out
    assert "ocr" in out


def test_doctor_json(capsys, qapp):
    assert cli.main(["doctor", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    capabilities = {item["capability"] for item in data}
    assert {"platform", "capture", "list_windows", "ocr"} <= capabilities
    assert all("available" in item for item in data)


# --- mcp --------------------------------------------------------------------


@pytest.mark.parametrize("timeout", ["0", "-5"])
def test_mcp_rejects_non_positive_timeout(timeout, capsys):
    # 0 would silently mean "no timeout" and a negative value would blow up
    # in signal.alarm — refuse both as the usage errors they are.
    assert cli.main(["mcp", "--timeout", timeout]) == 2
    assert "--timeout" in capsys.readouterr().err


# --- headless helpers -------------------------------------------------------


def test_select_window_is_case_insensitive():
    windows = FakeCapturer().windows
    window, matched = headless.select_window(windows, "SAFARI")
    assert window.window_id == 11
    assert matched == 2


def test_parse_region_round_trip():
    assert headless.parse_region(" 1, 2, 3, 4 ") == Rect(x=1, y=2, width=3, height=4)
