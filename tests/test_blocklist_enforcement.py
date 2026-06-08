# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Blocklist enforcement in the shared capture path (refusal half).

Full-screen redaction lands separately; here a *window* or *app* capture of a
blocked app is refused with exit code 6, while an empty blocklist takes the
exact same path as before.
"""

from __future__ import annotations

import pytest

from shotquill import audit, headless, paths
from shotquill import blocklist as bl
from shotquill.capture.base import CaptureResult, Rect, WindowInfo


class FakeCapturer:
    def __init__(self, windows=None, list_raises=None):
        self.include_cursor = False
        self._windows = windows or []
        self._list_raises = list_raises
        self.captured = []

    def _result(self):
        return CaptureResult(width=2, height=2, scale=1.0, pixels=bytes([0] * 16))

    def capture_fullscreen(self):
        self.captured.append("fullscreen")
        return self._result()

    def capture_region(self, region):
        self.captured.append(("region", region))
        return self._result()

    def capture_window(self, window_id):
        self.captured.append(("window", window_id))
        return self._result()

    def list_windows(self):
        if self._list_raises is not None:
            raise self._list_raises
        return self._windows


SAFARI = WindowInfo(11, "Safari", "GitHub", Rect(0, 0, 80, 60), bundle_id="com.apple.safari")
ONEPW = WindowInfo(
    22, "1Password", "Vault", Rect(0, 0, 80, 60), bundle_id="com.1password.1password"
)

ONEPW_LIST = bl.Blocklist((bl.BlockRule(bundle_id="com.1password.1password"),))


@pytest.fixture(autouse=True)
def _quiet_audit(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "audit_log_path", lambda: tmp_path / "audit.log")
    monkeypatch.setattr(audit, "_to_system_log", lambda line: None)
    monkeypatch.setattr(audit, "_caller_chain", lambda: ["pytest"])


def test_app_capture_of_blocked_app_is_refused():
    cap = FakeCapturer(windows=[ONEPW])
    with pytest.raises(headless.CaptureBlocked) as exc:
        headless.perform_capture(cap, app="1password", blocklist=ONEPW_LIST)
    assert exc.value.exit_code == headless.EXIT_BLOCKED
    assert cap.captured == []  # never reached the capture call


def test_app_capture_of_allowed_app_proceeds():
    cap = FakeCapturer(windows=[SAFARI])
    result, target, _ = headless.perform_capture(cap, app="safari", blocklist=ONEPW_LIST)
    assert isinstance(result, CaptureResult)
    assert "Safari" in target


def test_window_id_capture_of_blocked_window_is_refused():
    cap = FakeCapturer(windows=[ONEPW])
    with pytest.raises(headless.CaptureBlocked):
        headless.perform_capture(cap, window_id=22, blocklist=ONEPW_LIST)
    assert cap.captured == []


def test_empty_blocklist_skips_enumeration_entirely():
    # An empty blocklist must not change the by-id path at all — proven by a
    # capturer whose list_windows would blow up if it were consulted.
    cap = FakeCapturer(list_raises=RuntimeError("must not enumerate"))
    result, target, _ = headless.perform_capture(cap, window_id=22, blocklist=bl.Blocklist())
    assert cap.captured == [("window", 22)]
    assert target == "window 22"


def test_window_id_capture_when_enumeration_unavailable_proceeds():
    # If windows cannot be listed we cannot match the id — fail open and let
    # the capture itself succeed or fail.
    cap = FakeCapturer(list_raises=headless.CapabilityUnsupported("list_windows", "wayland"))
    result, _, _ = headless.perform_capture(cap, window_id=22, blocklist=ONEPW_LIST)
    assert cap.captured == [("window", 22)]


def test_fullscreen_is_not_refused_even_with_blocked_app_present():
    # Full-screen redaction is a separate step; refusal must not fire here, or
    # one sensitive window would block grabbing the whole screen.
    cap = FakeCapturer(windows=[ONEPW])
    result, target, _ = headless.perform_capture(cap, blocklist=ONEPW_LIST)
    assert target == "fullscreen"


def test_blocked_capture_is_audited(tmp_path, monkeypatch):
    log = tmp_path / "audit.log"
    monkeypatch.setattr(paths, "audit_log_path", lambda: log)
    cap = FakeCapturer(windows=[ONEPW])
    with pytest.raises(headless.CaptureBlocked):
        headless.perform_capture(cap, app="1password", blocklist=ONEPW_LIST, via="mcp")
    line = log.read_text(encoding="utf-8")
    assert '"capture_blocked"' in line and '"via": "mcp"' in line and "1Password" in line


def test_active_blocklist_fails_closed_on_corrupt_file(monkeypatch, tmp_path):
    path = tmp_path / "blocklist.json"
    path.write_text("{bad json", encoding="utf-8")
    monkeypatch.setattr(paths, "blocklist_path", lambda: path)
    with pytest.raises(headless.CaptureBlocked):
        headless.active_blocklist()


def test_doctor_reports_blocklist(monkeypatch, tmp_path):
    path = tmp_path / "blocklist.json"
    bl.save(ONEPW_LIST, path)
    monkeypatch.setattr(paths, "blocklist_path", lambda: path)
    entry = next(c for c in headless.doctor_checks() if c["capability"] == "app_blocklist")
    assert entry["available"] is True
    assert "com.1password.1password" in entry["detail"]
