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
    def __init__(self, windows=None, list_raises=None, can_exclude=False):
        self.include_cursor = False
        self._windows = windows or []
        self._list_raises = list_raises
        # When True, model a backend that omits windows at capture time (macOS
        # ScreenCaptureKit); otherwise model the legacy path that cannot, so the
        # caller falls back to a solid block.
        self._can_exclude = can_exclude
        self.captured = []
        self.excluded = frozenset()

    def _result(self, excluded=frozenset()):
        # Non-black fill so redaction (which paints opaque black) is observable.
        return CaptureResult(
            width=2, height=2, scale=1.0, pixels=bytes([200] * 16), excluded_window_ids=excluded
        )

    def capture_fullscreen(self, exclude_window_ids=frozenset()):
        self.captured.append("fullscreen")
        self.excluded = frozenset(exclude_window_ids) if self._can_exclude else frozenset()
        return self._result(self.excluded)

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


def test_doctor_flags_unredacted_blocklist_without_enumeration(monkeypatch, tmp_path):
    # The honest part: on a backend that can't enumerate windows (Linux), a
    # blocked app is captured plainly in full-screen grabs — doctor must say so.
    path = tmp_path / "blocklist.json"
    bl.save(ONEPW_LIST, path)
    monkeypatch.setattr(paths, "blocklist_path", lambda: path)
    check = headless._check_blocklist_redaction(can_enumerate=False)
    assert check is not None and check["available"] is False
    assert "NOT redacted" in check["detail"]


def test_doctor_blocklist_redaction_ok_with_enumeration(monkeypatch, tmp_path):
    path = tmp_path / "blocklist.json"
    bl.save(ONEPW_LIST, path)
    monkeypatch.setattr(paths, "blocklist_path", lambda: path)
    check = headless._check_blocklist_redaction(can_enumerate=True)
    assert check is not None and check["available"] is True


def test_doctor_blocklist_redaction_silent_without_rules_or_capture(monkeypatch, tmp_path):
    path = tmp_path / "blocklist.json"  # never written → empty list, nothing to protect
    monkeypatch.setattr(paths, "blocklist_path", lambda: path)
    assert headless._check_blocklist_redaction(can_enumerate=False) is None
    # Capture unavailable (None) is also silent — the capture check tells that story.
    bl.save(ONEPW_LIST, path)
    assert headless._check_blocklist_redaction(can_enumerate=None) is None


# --- full-screen / region redaction -----------------------------------------


def _all_black(result):
    return all(b == (0 if i % 4 < 3 else 255) for i, b in enumerate(result.pixels))


def test_fullscreen_redacts_blocked_window(tmp_path, monkeypatch):
    log = tmp_path / "audit.log"
    monkeypatch.setattr(paths, "audit_log_path", lambda: log)
    cap = FakeCapturer(windows=[ONEPW])  # 1Password fills the (tiny) frame
    result, target, _ = headless.perform_capture(cap, blocklist=ONEPW_LIST, via="cli")
    assert target == "fullscreen"
    assert _all_black(result)  # the sensitive pixels are gone, not overlaid
    assert "capture_redacted" in log.read_text(encoding="utf-8")


def test_fullscreen_excludes_blocked_window_without_painting(tmp_path, monkeypatch):
    # When the backend can omit the window from the capture itself (SCK), the
    # blocked app is simply absent — nothing is painted, so what was behind it
    # shows through and windows on top stay intact.
    log = tmp_path / "audit.log"
    monkeypatch.setattr(paths, "audit_log_path", lambda: log)
    cap = FakeCapturer(windows=[ONEPW], can_exclude=True)
    result, target, _ = headless.perform_capture(cap, blocklist=ONEPW_LIST, via="cli")
    assert target == "fullscreen"
    assert cap.excluded == frozenset({ONEPW.window_id})  # asked to omit the right window
    assert not _all_black(result)  # omitted at capture time, not painted over
    assert "capture_redacted" in log.read_text(encoding="utf-8")


def test_fullscreen_solid_blocks_window_the_backend_cannot_exclude(tmp_path, monkeypatch):
    # The fallback half: a backend that cannot omit windows (legacy path) still
    # gets the sensitive pixels painted out, so nothing leaks either way.
    log = tmp_path / "audit.log"
    monkeypatch.setattr(paths, "audit_log_path", lambda: log)
    cap = FakeCapturer(windows=[ONEPW], can_exclude=False)
    result, _, _ = headless.perform_capture(cap, blocklist=ONEPW_LIST, via="cli")
    assert cap.excluded == frozenset()  # the backend reported omitting nothing
    assert _all_black(result)  # so the window is solid-blocked instead
    assert "capture_redacted" in log.read_text(encoding="utf-8")


def test_region_redacts_blocked_window():
    cap = FakeCapturer(windows=[ONEPW])
    result, target, _ = headless.perform_capture(cap, region=Rect(0, 0, 2, 2), blocklist=ONEPW_LIST)
    assert target.startswith("region")
    assert _all_black(result)


def test_fullscreen_unchanged_when_no_blocked_window_present():
    cap = FakeCapturer(windows=[SAFARI])
    result, _, _ = headless.perform_capture(cap, blocklist=ONEPW_LIST)
    assert not _all_black(result)  # Safari is not blocked; nothing painted


def test_fullscreen_redaction_gap_is_logged_when_enumeration_unavailable(tmp_path, monkeypatch):
    log = tmp_path / "audit.log"
    monkeypatch.setattr(paths, "audit_log_path", lambda: log)
    cap = FakeCapturer(list_raises=headless.CapabilityUnsupported("list_windows", "wayland"))
    result, _, _ = headless.perform_capture(cap, blocklist=ONEPW_LIST)
    assert not _all_black(result)  # cannot enumerate → frame left as-is
    assert "redact_unavailable" in log.read_text(encoding="utf-8")


def test_fullscreen_redaction_respects_reported_origin():
    # A multi-monitor grab whose top-left is logical (100, 50): a blocked window
    # at (100, 50) must map to the image's (0, 0), not be shifted off-frame. With
    # the old (0, 0) assumption nothing would be redacted, so this pins the fix.
    class OffsetCapturer:
        include_cursor = False

        def list_windows(self):
            return [
                WindowInfo(
                    1, "1Password", "", Rect(100, 50, 2, 2), bundle_id="com.1password.1password"
                )
            ]

        def capture_fullscreen(self, exclude_window_ids=frozenset()):
            return CaptureResult(
                width=2, height=2, scale=1.0, pixels=bytes([200] * 16), origin_x=100, origin_y=50
            )

    result, _, _ = headless.perform_capture(OffsetCapturer(), blocklist=ONEPW_LIST)
    assert _all_black(result)
