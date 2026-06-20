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
import os
import sys
import types

import pytest

from shotquill import audit, cli, headless, paths
from shotquill.capture.base import CaptureResult, DisplayInfo, Rect, WindowInfo

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
        self.displays = [
            DisplayInfo(
                index=0, name="built-in", bounds=Rect(0, 0, 1440, 900), scale=2.0, primary=True
            ),
            DisplayInfo(index=1, name="external", bounds=Rect(1440, -180, 1920, 1080)),
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

    def capture_interactive(self) -> CaptureResult:
        self.calls.append(("interactive",))
        return _result()

    def list_windows(self) -> list[WindowInfo]:
        return self.windows

    def list_displays(self) -> list[DisplayInfo]:
        return self.displays


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
    assert os.path.isabs(out)  # absolute (``/...`` on POSIX, ``C:\...`` on Windows)
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


def test_capture_empty_region_is_usage_error_not_fullscreen(fake_capturer, capsys):
    # An explicit empty --region is falsy and must not silently fall through to
    # a full-screen grab (same hazard guarded for --app).
    assert cli.main(["capture", "--region", ""]) == 2
    assert fake_capturer.calls == []
    assert capsys.readouterr().err


def test_capture_title_without_app_is_usage_error(fake_capturer, capsys):
    assert cli.main(["capture", "--title", "x"]) == 2
    assert "--app" in capsys.readouterr().err


def test_capture_targets_are_mutually_exclusive(fake_capturer):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["capture", "--window-id", "1", "--app", "safari"])
    assert excinfo.value.code == 2


def test_capture_display_is_a_region_capture_of_its_bounds(fake_capturer, capsys):
    assert cli.main(["capture", "--display", "1"]) == 0
    # The external monitor sits right of the primary with a negative y — the
    # exact case a naive (0, 0)-anchored crop would get wrong.
    assert fake_capturer.calls == [("region", Rect(x=1440, y=-180, width=1920, height=1080))]


def test_capture_display_json_names_the_display(fake_capturer, capsys):
    assert cli.main(["capture", "--display", "0", "--json"]) == 0
    meta = json.loads(capsys.readouterr().out)
    assert meta["target"] == "display 0 (1440x900 at 0,0)"


def test_capture_unknown_display_exits_5(fake_capturer, capsys):
    assert cli.main(["capture", "--display", "9"]) == headless.EXIT_NO_MATCH
    assert "no display 9" in capsys.readouterr().err


def test_capture_interactive_routes_to_the_picker(fake_capturer, capsys, isolated_audit):
    assert cli.main(["capture", "--interactive"]) == 0
    assert fake_capturer.calls == [("interactive",)]


def test_capture_interactive_target_is_named_interactive(fake_capturer, capsys, isolated_audit):
    assert cli.main(["capture", "--interactive", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["target"] == "interactive"


@pytest.mark.parametrize(
    "target",
    [["--window-id", "1"], ["--app", "safari"], ["--region", "0,0,5,5"], ["--display", "0"]],
)
def test_capture_interactive_conflicts_with_a_target(fake_capturer, capsys, target):
    assert cli.main(["capture", "--interactive", *target]) == 2
    assert "--interactive picks the target itself" in capsys.readouterr().err
    assert fake_capturer.calls == []


def test_capture_interactive_refused_under_allowlist_exits_6(fake_capturer, tmp_path, capsys):
    from shotquill import allowlist as al
    from shotquill import blocklist as bl

    al.save(
        al.Allowlist(enabled=True, rules=(bl.BlockRule(name="firefox"),)),
        tmp_path / "allowlist.json",
    )
    assert cli.main(["capture", "--interactive"]) == headless.EXIT_BLOCKED
    assert "allowlist" in capsys.readouterr().err.lower()
    assert fake_capturer.calls == []  # refused before the picker is ever shown


def test_capture_interactive_unsupported_platform_exits_4(monkeypatch, capsys):
    pytest.importorskip("PySide6")
    from shotquill.capture.base import ScreenCapturer

    class NoPicker(ScreenCapturer):
        def capture_fullscreen(self, exclude_window_ids=frozenset()):
            raise AssertionError("should not capture")

        def capture_region(self, region):
            raise AssertionError("should not capture")

        def list_windows(self):
            return []

        def capture_window(self, window_id):
            raise AssertionError("should not capture")

    monkeypatch.setattr(headless, "get_capturer", lambda include_cursor=False: NoPicker())
    assert cli.main(["capture", "--interactive"]) == headless.EXIT_UNSUPPORTED
    assert "only supported on Wayland" in capsys.readouterr().err


def test_capture_display_excludes_other_targets(fake_capturer):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["capture", "--display", "0", "--region", "0,0,10,10"])
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


def test_capture_redact_pii_masks_the_matched_box(fake_capturer, monkeypatch, tmp_path):
    # FakeCapturer returns a 2x2 red frame; a recognized PII box at (0,0,1,1) must
    # come out masked (black) while the rest stays red.
    from PySide6.QtGui import QImage

    from shotquill.ocr.base import TextBox

    class _Recognizer:
        def recognize_boxes(self, image):
            return [TextBox("ada@example.com", 0, 0, 1, 1)]

    monkeypatch.setattr(headless, "get_recognizer", lambda: _Recognizer())
    dest = tmp_path / "shot.png"
    assert cli.main(["capture", "--redact-pii", "-o", str(dest)]) == 0
    img = QImage(str(dest))
    assert img.pixelColor(0, 0).getRgb()[:3] == (0, 0, 0)  # PII box → masked
    assert img.pixelColor(1, 1).getRgb()[:3] == (255, 0, 0)  # rest → intact


def test_capture_redact_pii_unsupported_exits_4(fake_capturer, monkeypatch, tmp_path):
    # OCR is required to find PII; a host without it fails fast (before capturing).
    def _nope():
        raise headless.CapabilityUnsupported("ocr", "requires macOS Vision")

    monkeypatch.setattr(headless, "get_recognizer", _nope)
    assert (
        cli.main(["capture", "--redact-pii", "-o", str(tmp_path / "x.png")])
        == headless.EXIT_UNSUPPORTED
    )


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


# --- capture: deterministic -------------------------------------------------


def test_capture_deterministic_conflicts_with_include_cursor(fake_capturer, capsys):
    assert cli.main(["capture", "--deterministic", "--include-cursor"]) == 2
    assert fake_capturer.calls == []  # rejected before any capture
    assert "conflict" in capsys.readouterr().err


def test_capture_deterministic_file_matches_stdout(fake_capturer, capsysbinary, tmp_path):
    # A saved file and a piped capture of the same scene must be byte-identical:
    # both go through the deterministic encode path, not just the stream.
    dest = tmp_path / "shot.png"
    assert cli.main(["capture", "--deterministic", "-o", str(dest)]) == 0
    capsysbinary.readouterr()  # discard the printed path before streaming bytes
    assert cli.main(["capture", "--deterministic", "-o", "-"]) == 0
    streamed = capsysbinary.readouterr().out
    assert streamed.startswith(PNG_MAGIC)
    assert dest.read_bytes() == streamed


def test_capture_deterministic_forces_cursor_off(monkeypatch, capsys, tmp_path):
    pytest.importorskip("PySide6")
    seen = {}

    def _record(include_cursor=False):
        seen["include_cursor"] = include_cursor
        return FakeCapturer()

    monkeypatch.setattr(headless, "get_capturer", _record)
    monkeypatch.setattr(paths, "capture_tmp_dir", lambda: tmp_path)
    assert cli.main(["capture", "--deterministic"]) == 0
    assert seen["include_cursor"] is False


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


def test_windows_table_strips_terminal_escapes_from_titles(fake_capturer, capsys):
    # A window's title/class are app-set; a hostile app must not be able to
    # inject ANSI/control sequences into the human table. JSON stays escaped.
    from shotquill.capture.base import Rect, WindowInfo

    fake_capturer.windows = [
        WindowInfo(
            window_id=1,
            owner="ev\x1b[31mil",
            title="t\x1b]0;pwn\x07x\nrest",
            bounds=Rect(0, 0, 10, 10),
        ),
    ]
    assert cli.main(["windows"]) == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\x07" not in out
    # Only the control bytes are dropped; the escapes' now-inert printable
    # leftovers stay as plain text.
    assert "ev[31mil" in out and "t]0;pwnxrest" in out


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


# --- allowlist management ---------------------------------------------------


def _load_allowlist(tmp_path):
    from shotquill import allowlist as al

    return al.load(tmp_path / "allowlist.json")


def test_allowlist_starts_disabled_and_empty(tmp_path, capsys):
    assert cli.main(["allowlist", "list"]) == 0
    out = capsys.readouterr().out
    assert "enabled: no" in out
    assert "(no rules)" in out


def test_allowlist_add_then_list(tmp_path, capsys):
    assert cli.main(["allowlist", "add", "--bundle-id", "com.apple.Terminal"]) == 0
    capsys.readouterr()
    assert cli.main(["allowlist", "add", "--name", "firefox"]) == 0
    capsys.readouterr()
    assert cli.main(["allowlist", "list"]) == 0
    out = capsys.readouterr().out
    assert "com.apple.Terminal" in out and "name~firefox" in out


def test_allowlist_enable_and_disable(tmp_path, capsys):
    cli.main(["allowlist", "add", "--name", "firefox"])
    capsys.readouterr()
    assert cli.main(["allowlist", "enable"]) == 0
    assert _load_allowlist(tmp_path).enabled is True
    assert cli.main(["allowlist", "disable"]) == 0
    loaded = _load_allowlist(tmp_path)
    assert loaded.enabled is False
    assert loaded.rules  # disabling keeps the rules


def test_allowlist_enable_with_no_rules_warns(tmp_path, capsys):
    assert cli.main(["allowlist", "enable"]) == 0
    assert "nothing can be captured" in capsys.readouterr().err
    assert _load_allowlist(tmp_path).enabled is True


def test_allowlist_list_json(tmp_path, capsys):
    cli.main(["allowlist", "add", "--bundle-id", "com.x"])
    cli.main(["allowlist", "enable"])
    capsys.readouterr()
    assert cli.main(["allowlist", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "enabled": True,
        "rules": [{"bundle_id": "com.x"}],
    }


def test_allowlist_remove(tmp_path, capsys):
    cli.main(["allowlist", "add", "--name", "firefox"])
    capsys.readouterr()
    assert cli.main(["allowlist", "remove", "--name", "firefox"]) == 0
    assert _load_allowlist(tmp_path).rules == ()


def test_doctor_reports_enabled_allowlist(fake_capturer, tmp_path, capsys):
    from shotquill import allowlist as al
    from shotquill import blocklist as bl

    al.save(
        al.Allowlist(enabled=True, rules=(bl.BlockRule(name="terminal"),)),
        tmp_path / "allowlist.json",
    )
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "app_allowlist" in out
    assert "ENABLED" in out


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


# --- displays ----------------------------------------------------------------


def test_displays_table(fake_capturer, capsys, isolated_audit):
    assert cli.main(["displays"]) == 0
    out = capsys.readouterr().out
    assert "INDEX" in out
    assert "1440x900 at 0,0" in out
    assert "(primary)" in out
    assert "1920x1080 at 1440,-180" in out
    entries = _audit_entries(isolated_audit)
    assert entries and entries[-1]["action"] == "displays"


def test_displays_json(fake_capturer, capsys):
    assert cli.main(["displays", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [d["index"] for d in data] == [0, 1]
    assert data[0]["primary"] is True and data[0]["scale"] == 2.0
    assert data[1]["bounds"] == {"x": 1440, "y": -180, "width": 1920, "height": 1080}


def test_displays_unsupported_exits_4(fake_capturer, capsys, monkeypatch):
    def _nope():
        raise headless.CapabilityUnsupported("displays", "no screens")

    monkeypatch.setattr(fake_capturer, "list_displays", _nope)
    assert cli.main(["displays"]) == headless.EXIT_UNSUPPORTED


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


def test_ocr_strips_control_chars_from_app_text(monkeypatch, capsys, tmp_path):
    # OCR text is app-controlled (pixels off the screen); terminal control
    # sequences must be stripped before printing, like the windows table does.
    class _Recognizer:
        def recognize(self, image):
            return ["safe\x1b[31mline", "tab\there"]

    monkeypatch.setattr(headless, "get_recognizer", lambda: _Recognizer())
    image = tmp_path / "shot.png"
    image.write_bytes(_png_bytes())
    assert cli.main(["ocr", str(image)]) == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\t" not in out
    assert out == "safe[31mline\ntabhere\n"


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


# --- install-desktop-entry --------------------------------------------------
#
# The command exists because ``pipx install shotquill`` lays the bundled
# .desktop and SVG inside its private venv (``<sys.prefix>/share/...``) where
# the freedesktop app menu never looks. ``squill install-desktop-entry`` is
# the one-liner that bridges the gap by copying into ~/.local/share.


def _stage_packaged_data(monkeypatch, tmp_path):
    """Pretend a wheel installed our data-files under ``sys.prefix/share/...``.

    Mirrors the layout setuptools produces from ``[tool.setuptools.data-files]``
    so the command's ``_locate_packaged_data`` finds the source files without
    an actual wheel build."""
    monkeypatch.setattr(cli.sys, "prefix", str(tmp_path / "prefix"))
    desktop = tmp_path / "prefix" / "share" / "applications" / "shotquill-gui.desktop"
    icon = (
        tmp_path / "prefix" / "share" / "icons" / "hicolor" / "scalable" / "apps" / "shotquill.svg"
    )
    desktop.parent.mkdir(parents=True, exist_ok=True)
    icon.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text("[Desktop Entry]\nName=ShotQuill\nExec=shotquill\n", encoding="utf-8")
    icon.write_text("<svg/>", encoding="utf-8")
    return desktop, icon


def _redirect_xdg_data_home(monkeypatch, tmp_path):
    home = tmp_path / "xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(home))
    return home


def test_install_desktop_entry_unsupported_off_linux(monkeypatch, capsys):
    # macOS / Windows have no freedesktop launcher — fail with the typed
    # capability code (4) so agents stop retrying instead of treating this
    # as a transient error.
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    rc = cli.main(["install-desktop-entry"])
    assert rc == headless.EXIT_UNSUPPORTED
    assert "Linux-only" in capsys.readouterr().err


def test_install_desktop_entry_unsupported_when_payload_missing(monkeypatch, capsys, tmp_path):
    # Editable installs without ``pip install`` proper (or a hand-built venv
    # that skipped data-files) don't have the bundled files. Refuse with a
    # clear hint instead of silently doing nothing.
    import site

    monkeypatch.setattr(cli.sys, "platform", "linux")
    monkeypatch.setattr(cli.sys, "prefix", str(tmp_path / "empty-prefix"))
    monkeypatch.setattr(site, "getuserbase", lambda: str(tmp_path / "empty-user"))
    _redirect_xdg_data_home(monkeypatch, tmp_path)
    rc = cli.main(["install-desktop-entry"])
    assert rc == headless.EXIT_UNSUPPORTED
    assert "bundled desktop files not found" in capsys.readouterr().err


def test_install_desktop_entry_copies_files_to_xdg_data_home(monkeypatch, tmp_path, capsys):
    # Happy path: bundled data files exist in ``<sys.prefix>/share/...`` (as
    # pipx leaves them), and the command lays them into ~/.local/share so
    # GNOME / KDE / XFCE find them via XDG_DATA_DIRS.
    monkeypatch.setattr(cli.sys, "platform", "linux")
    desktop_src, icon_src = _stage_packaged_data(monkeypatch, tmp_path)
    data_home = _redirect_xdg_data_home(monkeypatch, tmp_path)
    # The cache refresh shells out to update-desktop-database / gtk-update-icon-cache;
    # tests must not actually run those (they'd hit the real $PATH).
    monkeypatch.setattr(cli, "_refresh_desktop_caches", lambda *_args: None)

    rc = cli.main(["install-desktop-entry"])

    assert rc == 0
    dst_desktop = data_home / "applications" / "shotquill.desktop"
    dst_icon = data_home / "icons" / "hicolor" / "scalable" / "apps" / "shotquill.svg"
    assert dst_desktop.is_file()
    assert dst_icon.is_file()
    assert dst_desktop.read_text(encoding="utf-8") == desktop_src.read_text(encoding="utf-8")
    assert dst_icon.read_text(encoding="utf-8") == icon_src.read_text(encoding="utf-8")
    # The freedesktop spec resolves ``Icon=shotquill`` by basename, and the
    # .desktop id is the file basename — both destinations must drop any
    # ``-gui`` suffix and use the canonical names.
    assert dst_desktop.name == "shotquill.desktop"
    assert dst_icon.name == "shotquill.svg"
    out = capsys.readouterr().out
    assert "installed:" in out


def test_install_desktop_entry_is_idempotent(monkeypatch, tmp_path):
    # A second run after a first must overwrite to the same content — no
    # error, no "already installed" message-as-failure path.
    monkeypatch.setattr(cli.sys, "platform", "linux")
    _stage_packaged_data(monkeypatch, tmp_path)
    _redirect_xdg_data_home(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_refresh_desktop_caches", lambda *_args: None)
    assert cli.main(["install-desktop-entry"]) == 0
    assert cli.main(["install-desktop-entry"]) == 0  # second run also exits 0


def test_install_desktop_entry_print_paths_does_not_write(monkeypatch, tmp_path, capsys):
    # ``--print-paths`` is the diagnostic flag — useful for "what would this
    # do" without modifying the user's data home.
    monkeypatch.setattr(cli.sys, "platform", "linux")
    _stage_packaged_data(monkeypatch, tmp_path)
    data_home = _redirect_xdg_data_home(monkeypatch, tmp_path)

    rc = cli.main(["install-desktop-entry", "--print-paths"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "desktop:" in out and "icon:" in out
    assert not (data_home / "applications" / "shotquill.desktop").exists()
    assert not (data_home / "icons" / "hicolor" / "scalable" / "apps" / "shotquill.svg").exists()


# --- headless helpers -------------------------------------------------------


def test_select_window_is_case_insensitive():
    windows = FakeCapturer().windows
    window, matched = headless.select_window(windows, "SAFARI")
    assert window.window_id == 11
    assert matched == 2


def test_parse_region_round_trip():
    assert headless.parse_region(" 1, 2, 3, 4 ") == Rect(x=1, y=2, width=3, height=4)
