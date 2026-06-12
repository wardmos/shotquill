# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Display enumeration and `--display N` selection.

The Qt default ``list_displays`` runs under the offscreen platform (one fake
800x800 screen), which pins the enumeration contract — index 0, primary,
bounds in virtual-desktop space; multi-monitor geometry is exercised through
fakes because CI has exactly one screen.
"""

from __future__ import annotations

import pytest

from shotquill import audit, headless, paths
from shotquill.capture.base import CaptureResult, DisplayInfo, Rect

pytest.importorskip("PySide6")

PRIMARY = DisplayInfo(index=0, name="built-in", bounds=Rect(0, 0, 1440, 900), primary=True)
# Sits right of the primary with a negative y — the case a (0, 0)-anchored
# crop would get wrong.
EXTERNAL = DisplayInfo(index=1, name="external", bounds=Rect(1440, -180, 1920, 1080))


class FakeCapturer:
    def __init__(self, displays=None):
        self.include_cursor = False
        self.captured: list[tuple] = []
        self._displays = [PRIMARY, EXTERNAL] if displays is None else displays

    def _result(self):
        # Non-black fill so redaction (which paints opaque black) is observable.
        return CaptureResult(width=2, height=2, scale=1.0, pixels=bytes([200] * 16))

    def capture_fullscreen(self, exclude_window_ids=frozenset()):
        self.captured.append("fullscreen")
        return self._result()

    def capture_region(self, region):
        self.captured.append(("region", region))
        return self._result()

    def capture_window(self, window_id):
        self.captured.append(("window", window_id))
        return self._result()

    def list_windows(self):
        raise headless.CapabilityUnsupported("list_windows", "not implemented in this fake")

    def list_displays(self):
        return self._displays


@pytest.fixture(autouse=True)
def _quiet_audit(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "audit_log_path", lambda: tmp_path / "audit.log")
    monkeypatch.setattr(audit, "_to_system_log", lambda line: None)
    monkeypatch.setattr(audit, "_caller_chain", lambda: ["pytest"])


# --- select_display -----------------------------------------------------------


def test_select_display_by_index():
    assert headless.select_display([PRIMARY, EXTERNAL], 1) is EXTERNAL


def test_select_display_unknown_index_is_no_match():
    with pytest.raises(headless.DisplayNotFound) as exc:
        headless.select_display([PRIMARY, EXTERNAL], 7)
    assert exc.value.exit_code == headless.EXIT_NO_MATCH
    assert "0..1" in str(exc.value)  # names the valid range so callers can re-pick


def test_select_display_negative_index_is_no_match():
    with pytest.raises(headless.DisplayNotFound):
        headless.select_display([PRIMARY], -1)


# --- perform_capture(display=N) -------------------------------------------------


def test_display_capture_is_a_region_capture_of_its_bounds():
    cap = FakeCapturer()
    result, target, matched = headless.perform_capture(cap, display=1, blocklist=())
    assert cap.captured == [("region", EXTERNAL.bounds)]
    assert target == "display 1 (1920x1080 at 1440,-180)"
    assert matched == 1


def test_display_capture_unknown_index_raises_before_any_capture():
    cap = FakeCapturer()
    with pytest.raises(headless.DisplayNotFound):
        headless.perform_capture(cap, display=5, blocklist=())
    assert cap.captured == []


def test_display_capture_stale_bounds_is_no_match_not_invalid_arguments():
    # The index was valid at enumeration time; bounds the backend then rejects
    # (display unplugged, Wayland frame not covering it) must surface as the
    # typed re-list-and-re-pick error, not a raw ValueError that the CLI maps
    # to exit 1 and MCP mislabels invalid_arguments.
    class StaleCapturer(FakeCapturer):
        def capture_region(self, region):
            raise ValueError(f"region {region} is outside the virtual desktop")

    with pytest.raises(headless.DisplayNotFound) as exc:
        headless.perform_capture(StaleCapturer(), display=1, blocklist=())
    assert exc.value.exit_code == headless.EXIT_NO_MATCH
    assert "display 1" in str(exc.value)


def test_doctor_displays_check_survives_a_broken_backend():
    # The doctor reports problems; it must not crash on one. A capturer whose
    # list_displays blows up unexpectedly (e.g. a duck-typed fake without the
    # method) reads as unavailable, not as a doctor traceback.
    class NoDisplays:
        def __getattr__(self, name):
            raise AttributeError(name)

    check = headless._check_displays(NoDisplays())
    assert check["capability"] == "displays"
    assert check["available"] is False
    assert "probe" in check["detail"]


def test_display_capture_without_enumeration_logs_redaction_gap(monkeypatch, tmp_path):
    # With blocklist rules but no window enumeration the display frame cannot
    # be protected — same honest fallback as the region path: capture plainly
    # and log the gap.
    from shotquill import blocklist as bl

    rules = bl.Blocklist((bl.BlockRule(name="vault"),))
    log = tmp_path / "audit.log"
    monkeypatch.setattr(paths, "audit_log_path", lambda: log)
    cap = FakeCapturer()
    result, target, _ = headless.perform_capture(cap, display=0, blocklist=rules)
    assert cap.captured == [("region", PRIMARY.bounds)]
    assert "redact_unavailable" in log.read_text(encoding="utf-8")


# --- the Qt default list_displays (offscreen: one 800x800 screen) ---------------


def test_qt_default_enumerates_the_offscreen_screen(qapp):
    from shotquill.capture.qtgrab import QtGrabCapturer

    (display,) = QtGrabCapturer().list_displays()
    assert display.index == 0
    assert display.primary is True
    assert display.bounds.width > 0 and display.bounds.height > 0
    assert display.scale >= 1.0
    assert display.name


def test_qt_default_display_capture_round_trip(qapp):
    # End to end on the real backend: enumerate, then capture display 0 and
    # get pixels matching its bounds (the offscreen screen is the whole
    # virtual desktop, so this also equals a full-screen grab).
    from shotquill.capture.qtgrab import QtGrabCapturer

    cap = QtGrabCapturer()
    (display,) = cap.list_displays()
    result, target, _ = headless.perform_capture(cap, display=0, blocklist=())
    assert result.width == int(display.bounds.width * result.scale)
    assert result.height == int(display.bounds.height * result.scale)
    assert target.startswith("display 0 ")
