# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Allowlist enforcement in the shared capture path.

When the allowlist is enabled, a window/app capture is refused (exit code 6)
unless the target is on the list, and every whole-screen capture (fullscreen,
region, display) is refused outright. A disabled allowlist (the default) is
inert and takes the exact same path as before. Unlike the blocklist — which
fails *open* when windows cannot be enumerated, because an unmatchable window is
not provably sensitive — the allowlist fails *closed*: it permits by identity,
so a target it cannot verify is refused.
"""

from __future__ import annotations

import json

import pytest

from shotquill import allowlist as al
from shotquill import audit, headless, paths
from shotquill import blocklist as bl
from shotquill.capture.base import CaptureResult, Rect, WindowInfo


class FakeCapturer:
    def __init__(self, windows=None, list_raises=None, includes_overlaps=False):
        self.include_cursor = False
        self._windows = windows or []
        self._list_raises = list_raises
        # When True, model a no-compositor X11 grab whose window capture reads the
        # framebuffer, so windows stacked over the target bleed into the result.
        self._includes_overlaps = includes_overlaps
        self.captured = []

    def window_capture_includes_overlaps(self):
        return self._includes_overlaps

    def _result(self):
        return CaptureResult(
            width=2, height=2, scale=1.0, pixels=bytes([200] * 16), excluded_window_ids=frozenset()
        )

    def capture_fullscreen(self, exclude_window_ids=frozenset()):
        self.captured.append("fullscreen")
        return self._result()

    def capture_region(self, region):
        self.captured.append(("region", region))
        return self._result()

    def capture_window(self, window_id):
        self.captured.append(("window", window_id))
        return self._result()

    def capture_interactive(self):
        self.captured.append("interactive")
        return self._result()

    def list_windows(self):
        if self._list_raises is not None:
            raise self._list_raises
        return self._windows


SAFARI = WindowInfo(11, "Safari", "GitHub", Rect(0, 0, 80, 60), bundle_id="com.apple.safari")
TERMINAL = WindowInfo(12, "Terminal", "zsh", Rect(0, 0, 80, 60), bundle_id="com.apple.Terminal")

# Enabled allowlist that permits only Terminal.
TERM_ONLY = al.Allowlist(enabled=True, rules=(bl.BlockRule(bundle_id="com.apple.Terminal"),))
DISABLED = al.Allowlist(enabled=False, rules=(bl.BlockRule(bundle_id="com.apple.Terminal"),))
EMPTY = bl.Blocklist()  # an empty blocklist, passed alongside the allowlist under test


def _all_black(result):
    """Whether every pixel is opaque black — i.e. the frame was fully redacted."""
    return all(b == 0 for i, b in enumerate(result.pixels) if i % 4 != 3)


@pytest.fixture(autouse=True)
def _quiet_audit(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "audit_log_path", lambda: tmp_path / "audit.log")
    monkeypatch.setattr(audit, "_to_system_log", lambda line: None)
    monkeypatch.setattr(audit, "_caller_chain", lambda: ["pytest"])


# --- app captures -----------------------------------------------------------


def test_app_capture_of_allowed_app_proceeds():
    cap = FakeCapturer(windows=[TERMINAL])
    result, target, _ = headless.perform_capture(
        cap, app="terminal", blocklist=EMPTY, allowlist=TERM_ONLY
    )
    assert isinstance(result, CaptureResult)
    assert "Terminal" in target
    assert cap.captured == [("window", 12)]


def test_app_capture_of_not_allowed_app_is_refused():
    cap = FakeCapturer(windows=[SAFARI])
    with pytest.raises(headless.CaptureBlocked) as exc:
        headless.perform_capture(cap, app="safari", blocklist=EMPTY, allowlist=TERM_ONLY)
    assert exc.value.exit_code == headless.EXIT_BLOCKED
    assert cap.captured == []  # never reached the capture call


# --- window-id captures -----------------------------------------------------


def test_window_id_capture_of_allowed_window_proceeds():
    cap = FakeCapturer(windows=[TERMINAL])
    result, _, _ = headless.perform_capture(cap, window_id=12, blocklist=EMPTY, allowlist=TERM_ONLY)
    assert cap.captured == [("window", 12)]


def test_window_id_capture_of_not_allowed_window_is_refused():
    cap = FakeCapturer(windows=[SAFARI, TERMINAL])
    with pytest.raises(headless.CaptureBlocked):
        headless.perform_capture(cap, window_id=11, blocklist=EMPTY, allowlist=TERM_ONLY)
    assert cap.captured == []


def test_window_id_fails_closed_when_enumeration_unavailable():
    # The allowlist permits by identity; if windows cannot be listed we cannot
    # confirm the target is allowed, so we refuse rather than capture blindly.
    cap = FakeCapturer(list_raises=headless.CapabilityUnsupported("list_windows", "wayland"))
    with pytest.raises(headless.CaptureBlocked):
        headless.perform_capture(cap, window_id=12, blocklist=EMPTY, allowlist=TERM_ONLY)
    assert cap.captured == []


def test_window_id_fails_closed_when_id_not_among_windows():
    # The id is not in the enumerated set, so it cannot be matched → refuse.
    cap = FakeCapturer(windows=[TERMINAL])
    with pytest.raises(headless.CaptureBlocked):
        headless.perform_capture(cap, window_id=999, blocklist=EMPTY, allowlist=TERM_ONLY)
    assert cap.captured == []


# --- whole-screen captures are refused outright -----------------------------


def test_fullscreen_is_refused_when_allowlist_enabled():
    cap = FakeCapturer(windows=[TERMINAL])
    with pytest.raises(headless.CaptureBlocked) as exc:
        headless.perform_capture(cap, blocklist=EMPTY, allowlist=TERM_ONLY)
    assert exc.value.exit_code == headless.EXIT_BLOCKED
    assert cap.captured == []


def test_region_is_refused_when_allowlist_enabled():
    cap = FakeCapturer(windows=[TERMINAL])
    with pytest.raises(headless.CaptureBlocked):
        headless.perform_capture(
            cap, region=Rect(0, 0, 10, 10), blocklist=EMPTY, allowlist=TERM_ONLY
        )
    assert cap.captured == []


def test_display_is_refused_when_allowlist_enabled():
    cap = FakeCapturer(windows=[TERMINAL])
    with pytest.raises(headless.CaptureBlocked):
        headless.perform_capture(cap, display=0, blocklist=EMPTY, allowlist=TERM_ONLY)
    assert cap.captured == []


def test_interactive_is_refused_when_allowlist_enabled():
    # The compositor picker can land on any window or the whole screen, so the
    # "only these apps" allowlist refuses it outright — same as a fullscreen grab.
    cap = FakeCapturer(windows=[TERMINAL])
    with pytest.raises(headless.CaptureBlocked) as exc:
        headless.perform_interactive_capture(cap, allowlist=TERM_ONLY)
    assert exc.value.exit_code == headless.EXIT_BLOCKED
    assert cap.captured == []  # refused before the picker is ever shown


def test_interactive_proceeds_when_allowlist_disabled():
    cap = FakeCapturer(windows=[TERMINAL])
    result, target, matched = headless.perform_interactive_capture(cap, allowlist=DISABLED)
    assert isinstance(result, CaptureResult)
    assert (target, matched) == ("interactive", 1)
    assert cap.captured == ["interactive"]


# --- disabled allowlist is inert --------------------------------------------


def test_disabled_allowlist_does_not_refuse_anything():
    cap = FakeCapturer(windows=[SAFARI])
    # A non-allowed app, fullscreen, region — all proceed when disabled.
    headless.perform_capture(cap, app="safari", blocklist=EMPTY, allowlist=DISABLED)
    headless.perform_capture(cap, blocklist=EMPTY, allowlist=DISABLED)
    headless.perform_capture(cap, region=Rect(0, 0, 5, 5), blocklist=EMPTY, allowlist=DISABLED)
    assert ("window", 11) in cap.captured
    assert "fullscreen" in cap.captured


def test_disabled_allowlist_skips_enumeration_on_window_id():
    # Disabled allowlist + empty blocklist must not enumerate at all.
    cap = FakeCapturer(list_raises=RuntimeError("must not enumerate"))
    headless.perform_capture(cap, window_id=12, blocklist=EMPTY, allowlist=DISABLED)
    assert cap.captured == [("window", 12)]


# --- enabled-but-empty allowlist is a full lockdown -------------------------


def test_enabled_empty_allowlist_refuses_everything():
    cap = FakeCapturer(windows=[TERMINAL])
    locked = al.Allowlist(enabled=True, rules=())
    with pytest.raises(headless.CaptureBlocked):
        headless.perform_capture(cap, app="terminal", blocklist=EMPTY, allowlist=locked)
    with pytest.raises(headless.CaptureBlocked):
        headless.perform_capture(cap, blocklist=EMPTY, allowlist=locked)
    assert cap.captured == []


# --- interaction with the blocklist -----------------------------------------


def test_blocklist_still_wins_over_allowlist():
    # Terminal is allowed, but also blocklisted → refused (must pass both gates).
    cap = FakeCapturer(windows=[TERMINAL])
    block_term = bl.Blocklist((bl.BlockRule(bundle_id="com.apple.Terminal"),))
    with pytest.raises(headless.CaptureBlocked):
        headless.perform_capture(cap, app="terminal", blocklist=block_term, allowlist=TERM_ONLY)
    assert cap.captured == []


# --- overlap redaction on the no-compositor X11 window grab -----------------
#
# Capturing an allowed window on a framebuffer-read backend also reads any window
# stacked over it. A non-allowlisted window must be painted out, or the allowlist
# leaks the very apps it exists to keep out.


def test_window_capture_redacts_not_allowed_overlap_on_framebuffer_backend():
    # Terminal is allowed; Safari (not on the allowlist) is stacked over it. The
    # allowed target is captured, but Safari's overlapping pixels are painted out.
    cap = FakeCapturer(windows=[TERMINAL, SAFARI], includes_overlaps=True)
    result, _, _ = headless.perform_capture(cap, window_id=12, blocklist=EMPTY, allowlist=TERM_ONLY)
    assert cap.captured == [("window", 12)]
    assert _all_black(result)  # the non-allowed Safari overlap is gone


def test_app_capture_redacts_not_allowed_overlap_on_framebuffer_backend():
    cap = FakeCapturer(windows=[TERMINAL, SAFARI], includes_overlaps=True)
    result, target, _ = headless.perform_capture(
        cap, app="terminal", blocklist=EMPTY, allowlist=TERM_ONLY
    )
    assert "Terminal" in target
    assert _all_black(result)


def test_window_capture_keeps_overlap_on_surface_backend():
    # Surface-accurate backend: the grab sees only Terminal's own pixels, so a
    # non-allowed window over it is irrelevant — nothing is redacted.
    cap = FakeCapturer(windows=[TERMINAL, SAFARI], includes_overlaps=False)
    result, _, _ = headless.perform_capture(cap, window_id=12, blocklist=EMPTY, allowlist=TERM_ONLY)
    assert not _all_black(result)


def test_window_capture_ignores_non_overlapping_not_allowed_window():
    far = WindowInfo(99, "Safari", "x", Rect(500, 500, 80, 60), bundle_id="com.apple.safari")
    cap = FakeCapturer(windows=[TERMINAL, far], includes_overlaps=True)
    result, _, _ = headless.perform_capture(cap, window_id=12, blocklist=EMPTY, allowlist=TERM_ONLY)
    assert not _all_black(result)  # the non-allowed window doesn't intersect the target


# --- audit + safety ---------------------------------------------------------


def test_refusal_records_capture_not_allowed(tmp_path, monkeypatch):
    recorded = []
    monkeypatch.setattr(audit, "record", lambda action, **kw: recorded.append((action, kw)))
    cap = FakeCapturer(windows=[SAFARI])
    with pytest.raises(headless.CaptureBlocked):
        headless.perform_capture(cap, app="safari", blocklist=EMPTY, allowlist=TERM_ONLY)
    assert any(a == "capture_not_allowed" for a, _ in recorded)


def test_not_allowed_refusal_strips_control_chars_from_owner():
    evil = WindowInfo(33, "ev\x1b]0;pwned\x07il", "x", Rect(0, 0, 80, 60), bundle_id="com.evil.app")
    cap = FakeCapturer(windows=[evil])
    with pytest.raises(headless.CaptureBlocked) as exc:
        headless.perform_capture(cap, window_id=33, blocklist=EMPTY, allowlist=TERM_ONLY)
    assert "\x1b" not in str(exc.value)
    assert "\x07" not in str(exc.value)


# --- active_allowlist fails closed on a corrupt file ------------------------


def test_active_allowlist_fails_closed_on_corrupt_file(tmp_path, monkeypatch):
    path = tmp_path / "allowlist.json"
    path.write_text("{bad json")
    monkeypatch.setattr(paths, "allowlist_path", lambda: path)
    with pytest.raises(headless.CaptureBlocked):
        headless.active_allowlist()


def test_active_allowlist_loads_when_enabled(tmp_path, monkeypatch):
    path = tmp_path / "allowlist.json"
    al.save(al.Allowlist(enabled=True, rules=(bl.BlockRule(name="terminal"),)), path)
    monkeypatch.setattr(paths, "allowlist_path", lambda: path)
    loaded = headless.active_allowlist()
    assert loaded.enabled
    assert json.loads(path.read_text())["enabled"] is True
