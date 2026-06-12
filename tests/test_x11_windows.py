# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""X11 window enumeration: the pure filter/order logic and the backend wiring.

The Xlib shim (``_connect`` / ``_read_raw``) needs a live server, a window
manager, and python-xlib, none of which the headless test platforms have — so
it is exercised only on a real X11 desktop. Everything that makes a *decision*
(what to skip, how to order, how raw properties become a WindowInfo) lives in
``windows_from_raw`` and is tested here with plain data, the same split
``redact.py`` uses.
"""

from __future__ import annotations

import pytest

from shotquill import headless
from shotquill.capture.base import Rect, WindowInfo
from shotquill.capture.x11 import RawWindow, to_logical_bounds, windows_from_raw

OWN_PID = 4242


def _raw(window_id, **kw):
    base = dict(
        title="Title",
        wm_class="App",
        pid=10,
        bounds=Rect(0, 0, 100, 80),
        viewable=True,
        window_type="_NET_WM_WINDOW_TYPE_NORMAL",
    )
    base.update(kw)
    return RawWindow(window_id=window_id, **base)


# --- windows_from_raw: mapping ------------------------------------------------


def test_raw_window_becomes_windowinfo_with_class_as_owner_and_bundle():
    (win,) = windows_from_raw(
        [_raw(7, wm_class="Firefox", title="Inbox")], OWN_PID, from_stacking=False
    )
    assert win == WindowInfo(
        window_id=7,
        owner="Firefox",
        title="Inbox",
        bounds=Rect(0, 0, 100, 80),
        bundle_id="Firefox",
    )


def test_missing_wm_class_leaves_owner_blank_and_bundle_none():
    (win,) = windows_from_raw([_raw(7, wm_class=None)], OWN_PID, from_stacking=False)
    assert win.owner == ""
    assert win.bundle_id is None


# --- windows_from_raw: filtering ----------------------------------------------


def test_own_windows_are_excluded():
    raws = [_raw(1, pid=OWN_PID), _raw(2, pid=99)]
    assert [w.window_id for w in windows_from_raw(raws, OWN_PID, from_stacking=False)] == [2]


def test_unknown_pid_is_kept():
    # A window with no _NET_WM_PID can't be ours to rule out; keep it.
    (win,) = windows_from_raw([_raw(1, pid=None)], OWN_PID, from_stacking=False)
    assert win.window_id == 1


def test_unviewable_windows_are_excluded():
    raws = [_raw(1, viewable=False), _raw(2, viewable=True)]
    assert [w.window_id for w in windows_from_raw(raws, OWN_PID, from_stacking=False)] == [2]


@pytest.mark.parametrize(
    "wtype",
    ["_NET_WM_WINDOW_TYPE_DESKTOP", "_NET_WM_WINDOW_TYPE_DOCK"],
)
def test_desktop_and_dock_chrome_are_excluded(wtype):
    assert windows_from_raw([_raw(1, window_type=wtype)], OWN_PID, from_stacking=False) == []


def test_normal_dialog_and_untyped_windows_are_kept():
    raws = [
        _raw(1, window_type="_NET_WM_WINDOW_TYPE_NORMAL"),
        _raw(2, window_type="_NET_WM_WINDOW_TYPE_DIALOG"),
        _raw(3, window_type=None),  # legacy app with no type hint
    ]
    assert [w.window_id for w in windows_from_raw(raws, OWN_PID, from_stacking=False)] == [1, 2, 3]


@pytest.mark.parametrize("bounds", [Rect(0, 0, 0, 50), Rect(0, 0, 50, 0)])
def test_degenerate_sizes_are_excluded(bounds):
    assert windows_from_raw([_raw(1, bounds=bounds)], OWN_PID, from_stacking=False) == []


# --- windows_from_raw: ordering -----------------------------------------------


def test_stacking_order_is_reversed_to_front_most_first():
    # _NET_CLIENT_LIST_STACKING is bottom-to-top; the interface promises
    # front-most first.
    raws = [_raw(1), _raw(2), _raw(3)]  # 1 back ... 3 front
    order = [w.window_id for w in windows_from_raw(raws, OWN_PID, from_stacking=True)]
    assert order == [3, 2, 1]


def test_non_stacking_order_is_preserved():
    raws = [_raw(1), _raw(2), _raw(3)]
    order = [w.window_id for w in windows_from_raw(raws, OWN_PID, from_stacking=False)]
    assert order == [1, 2, 3]


def test_reversal_happens_after_filtering():
    # The dropped window must not leave a hole or shift the reversal.
    raws = [_raw(1), _raw(2, viewable=False), _raw(3)]
    order = [w.window_id for w in windows_from_raw(raws, OWN_PID, from_stacking=True)]
    assert order == [3, 1]


# --- to_logical_bounds: physical -> logical rescale ---------------------------


def test_logical_bounds_are_unchanged_at_dpr_1():
    windows = [WindowInfo(window_id=1, owner="A", title="t", bounds=Rect(10, 20, 30, 40))]
    assert to_logical_bounds(windows, 1.0) == windows


def test_logical_bounds_divide_by_dpr():
    # A 4K-scaled (dpr 2) window at physical 1000,600 sized 800x400 maps to the
    # logical 500,300 / 400x200 that the capture origin and scale assume — so
    # the redaction block lands on the window instead of past it.
    windows = [WindowInfo(window_id=1, owner="A", title="t", bounds=Rect(1000, 600, 800, 400))]
    (win,) = to_logical_bounds(windows, 2.0)
    assert win.bounds == Rect(500, 300, 400, 200)


def test_logical_bounds_round_outward_to_never_undercover():
    # Odd physical extents must not shrink inside the real window when halved —
    # a blocklist block that under-covers would leak the uncovered strip.
    windows = [WindowInfo(window_id=1, owner="A", title="t", bounds=Rect(1, 1, 3, 3))]
    (win,) = to_logical_bounds(windows, 2.0)
    # x: floor(1/2)=0, right: ceil(4/2)=2 -> covers logical 0..2 (>= the window)
    assert win.bounds == Rect(0, 0, 2, 2)


# --- backend wiring -----------------------------------------------------------


@pytest.fixture
def capturer(qapp):
    from shotquill.capture.qtgrab import QtGrabCapturer

    return QtGrabCapturer()


def test_list_windows_delegates_to_x11(capturer, monkeypatch):
    sentinel = [WindowInfo(window_id=1, owner="A", title="t", bounds=Rect(0, 0, 1, 1))]
    monkeypatch.setattr("shotquill.capture.x11.list_windows", lambda: sentinel)
    # Pin dpr=1 so the rescale is a no-op and the list passes through unchanged.
    monkeypatch.setattr(type(capturer), "_capture_dpr", staticmethod(lambda: 1.0))
    assert capturer.list_windows() == sentinel


def test_list_windows_rescales_physical_bounds_to_logical(capturer, monkeypatch):
    raw = [WindowInfo(window_id=1, owner="A", title="t", bounds=Rect(1000, 600, 800, 400))]
    monkeypatch.setattr("shotquill.capture.x11.list_windows", lambda: raw)
    monkeypatch.setattr(type(capturer), "_capture_dpr", staticmethod(lambda: 2.0))
    (win,) = capturer.list_windows()
    assert win.bounds == Rect(500, 300, 400, 200)


def test_list_windows_propagates_unsupported(capturer, monkeypatch):
    def boom():
        raise headless.CapabilityUnsupported("list_windows", "no WM")

    monkeypatch.setattr("shotquill.capture.x11.list_windows", boom)
    with pytest.raises(headless.CapabilityUnsupported):
        capturer.list_windows()


def test_capture_window_unknown_id_raises_runtime_error(capturer, monkeypatch):
    monkeypatch.setattr("shotquill.capture.x11.list_windows", list)  # no windows
    with pytest.raises(RuntimeError, match="not on screen"):
        capturer.capture_window(99)


def test_capture_window_grabs_by_id_with_window_bounds(capturer, monkeypatch):
    window = WindowInfo(window_id=7, owner="A", title="t", bounds=Rect(40, 30, 100, 80))
    monkeypatch.setattr("shotquill.capture.x11.list_windows", lambda: [window])
    seen = {}

    def fake_grab(window_id, bounds):
        seen["args"] = (window_id, bounds)
        return "result"

    monkeypatch.setattr(type(capturer), "_grab_window_id", staticmethod(fake_grab))
    assert capturer.capture_window(7) == "result"
    assert seen["args"] == (7, Rect(40, 30, 100, 80))


def test_capture_window_propagates_unsupported(capturer, monkeypatch):
    def boom():
        raise headless.CapabilityUnsupported("list_windows", "no WM")

    monkeypatch.setattr("shotquill.capture.x11.list_windows", boom)
    with pytest.raises(headless.CapabilityUnsupported):
        capturer.capture_window(1)


# --- list_windows: error handling ---------------------------------------------


def test_unexpected_read_error_becomes_typed_unsupported(monkeypatch):
    # A server fault mid-read must surface as the typed unsupported signal
    # callers branch on, not a raw Xlib traceback.
    from shotquill.capture import x11

    monkeypatch.setattr(x11, "_connect", lambda: _FakeDisplay())

    def boom(_display):
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(x11, "_read_raw", boom)
    with pytest.raises(headless.CapabilityUnsupported, match="reading the window list failed"):
        x11.list_windows()


def test_display_is_closed_even_when_read_fails(monkeypatch):
    from shotquill.capture import x11

    display = _FakeDisplay()
    monkeypatch.setattr(x11, "_connect", lambda: display)
    monkeypatch.setattr(x11, "_read_raw", lambda _d: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(headless.CapabilityUnsupported):
        x11.list_windows()
    assert display.closed


class _FakeDisplay:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True
