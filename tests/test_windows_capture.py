# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Windows window enumeration: pure filter/order logic and backend wiring.

The Win32 shim (``_enumerate_raw`` / ``_read_window``) needs a real Windows
session, which the headless test platform doesn't have — so it is exercised
only on Windows. Everything that makes a *decision* (what to skip, how raw
window fields become a WindowInfo, how bounds rescale) lives in
``windows_from_raw`` / ``to_logical_bounds`` and is tested here with plain data,
the same split ``x11.py`` and ``redact.py`` use.
"""

from __future__ import annotations

import pytest

from shotquill import headless
from shotquill.capture import windows as cap_windows
from shotquill.capture.base import Rect, WindowInfo
from shotquill.capture.windows import RawWindow, to_logical_bounds, windows_from_raw

OWN_PID = 4242


def _raw(hwnd, **kw):
    base = dict(
        title="Title",
        process_name="app.exe",
        pid=10,
        bounds=Rect(0, 0, 100, 80),
        visible=True,
        cloaked=False,
        tool_window=False,
    )
    base.update(kw)
    return RawWindow(hwnd=hwnd, **base)


# --- windows_from_raw: mapping ------------------------------------------------


def test_raw_window_becomes_windowinfo_with_exe_as_owner_and_bundle():
    (win,) = windows_from_raw([_raw(7, process_name="chrome.exe", title="Inbox")], OWN_PID)
    assert win == WindowInfo(
        window_id=7,
        owner="chrome.exe",
        title="Inbox",
        bounds=Rect(0, 0, 100, 80),
        bundle_id="chrome.exe",
    )


def test_missing_process_name_leaves_owner_blank_and_bundle_none():
    (win,) = windows_from_raw([_raw(7, process_name=None)], OWN_PID)
    assert win.owner == ""
    assert win.bundle_id is None


# --- windows_from_raw: filtering ----------------------------------------------


def test_own_windows_are_excluded():
    raws = [_raw(1, pid=OWN_PID), _raw(2, pid=99)]
    assert [w.window_id for w in windows_from_raw(raws, OWN_PID)] == [2]


def test_unknown_pid_is_kept():
    (win,) = windows_from_raw([_raw(1, pid=None)], OWN_PID)
    assert win.window_id == 1


def test_invisible_windows_are_excluded():
    raws = [_raw(1, visible=False), _raw(2, visible=True)]
    assert [w.window_id for w in windows_from_raw(raws, OWN_PID)] == [2]


def test_cloaked_windows_are_excluded():
    # DWM-cloaked windows (suspended UWP apps, virtual-desktop residents) are
    # listed by the shell but not really on screen.
    raws = [_raw(1, cloaked=True), _raw(2, cloaked=False)]
    assert [w.window_id for w in windows_from_raw(raws, OWN_PID)] == [2]


def test_tool_windows_are_excluded():
    raws = [_raw(1, tool_window=True), _raw(2, tool_window=False)]
    assert [w.window_id for w in windows_from_raw(raws, OWN_PID)] == [2]


def test_titleless_windows_are_excluded():
    # Windows keeps many captionless top-level windows (shell hosts, background
    # surfaces); the Alt-Tab heuristic — require a title — drops them.
    raws = [_raw(1, title=""), _raw(2, title="Real")]
    assert [w.window_id for w in windows_from_raw(raws, OWN_PID)] == [2]


@pytest.mark.parametrize("bad", [Rect(0, 0, 0, 80), Rect(0, 0, 100, 0)])
def test_degenerate_sizes_are_excluded(bad):
    raws = [_raw(1, bounds=bad), _raw(2, bounds=Rect(0, 0, 100, 80))]
    assert [w.window_id for w in windows_from_raw(raws, OWN_PID)] == [2]


def test_front_most_order_is_preserved():
    # EnumWindows already yields front-most first; the filter must not reorder.
    raws = [_raw(3), _raw(1), _raw(2)]
    assert [w.window_id for w in windows_from_raw(raws, OWN_PID)] == [3, 1, 2]


# --- to_logical_bounds --------------------------------------------------------


def test_to_logical_bounds_noop_at_unit_scale():
    wins = [WindowInfo(1, "a.exe", "t", Rect(10, 20, 100, 80), "a.exe")]
    assert to_logical_bounds(wins, 1.0) is wins


def test_to_logical_bounds_rescales_and_rounds_outward():
    wins = [WindowInfo(1, "a.exe", "t", Rect(10, 20, 101, 81), "a.exe")]
    (out,) = to_logical_bounds(wins, 2.0)
    # x: floor(10/2)=5, y: floor(20/2)=10; right: ceil(111/2)=56 -> w=51;
    # bottom: ceil(101/2)=51 -> h=41. Outward rounding never shrinks the rect.
    assert out.bounds == Rect(5, 10, 51, 41)


# --- backend wiring -----------------------------------------------------------


def test_list_windows_propagates_unsupported(monkeypatch):
    def boom():
        raise headless.CapabilityUnsupported("list_windows", "no Win32 here")

    monkeypatch.setattr(cap_windows, "_enumerate_raw", boom)
    with pytest.raises(headless.CapabilityUnsupported):
        cap_windows.list_windows()


def test_backend_list_windows_rescales_with_capture_dpr(qapp, monkeypatch):
    pytest.importorskip("PySide6")
    raw_logical = [WindowInfo(1, "a.exe", "t", Rect(0, 0, 200, 100), "a.exe")]
    monkeypatch.setattr(cap_windows, "list_windows", lambda: raw_logical)
    monkeypatch.setattr(
        cap_windows.WindowsScreenCapturer, "_capture_dpr", staticmethod(lambda: 2.0)
    )

    capturer = cap_windows.WindowsScreenCapturer()
    (win,) = capturer.list_windows()
    assert win.bounds == Rect(0, 0, 100, 50)  # rescaled by the capture dpr


def test_get_capturer_routes_to_windows_backend(qapp, monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setattr(headless.sys, "platform", "win32")
    assert isinstance(headless.get_capturer(), cap_windows.WindowsScreenCapturer)
