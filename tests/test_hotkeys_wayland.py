# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the Wayland GlobalShortcuts hotkey manager's bookkeeping and dispatch.

The live D-Bus work (open session, bind, subscribe, tear down) needs a real
Wayland portal, so :meth:`WaylandHotkeyManager._activate` / ``_deactivate`` are
the seam these stub out. What stays under test is everything around it: compiling
bindings into portal specs, the shortcut-id ↔ combo map, routing an ``Activated``
id back to its callback, and the start/stop lifecycle.
"""

import pytest

from shotquill.hotkeys import wayland
from shotquill.hotkeys.base import HotkeyUnavailable
from shotquill.hotkeys.wayland import WaylandHotkeyManager


class _StubManager(WaylandHotkeyManager):
    """A manager whose live-portal seam is replaced with a recorder, so the
    bookkeeping runs without a session bus."""

    def __init__(self):
        super().__init__()
        self.activated_specs = None
        self.deactivate_calls = 0

    def _activate(self, specs):
        self.activated_specs = specs
        self._session_handle = "/org/freedesktop/portal/desktop/session/1"

    def _deactivate(self):
        self.deactivate_calls += 1
        self._session_handle = None


class _Message:
    """Stand-in for the QDBusMessage an ``Activated`` signal delivers."""

    def __init__(self, *args):
        self._args = list(args)

    def arguments(self):
        return self._args


def test_register_and_unregister():
    manager = _StubManager()
    cb = lambda: None  # noqa: E731
    manager.register("<ctrl>+<shift>+s", cb)
    manager.register("<ctrl>+<shift>+a", cb)
    manager.unregister("<ctrl>+<shift>+a")
    assert set(manager._bindings) == {"<ctrl>+<shift>+s"}


def test_clear_removes_all_bindings():
    manager = _StubManager()
    manager.register("<ctrl>+a", lambda: None)
    manager.clear()
    assert manager._bindings == {}


def test_start_builds_portal_specs_and_id_map():
    manager = _StubManager()
    manager.register("<cmd>+<shift>+a", lambda: None, description="Smart capture")
    manager.start()
    assert manager.activated_specs == [
        ("sq_cmd_shift_a", "<cmd>+<shift>+a", "LOGO+SHIFT+a", "Smart capture")
    ]
    assert manager._ids == {"sq_cmd_shift_a": "<cmd>+<shift>+a"}


def test_description_defaults_to_the_combo():
    manager = _StubManager()
    manager.register("<alt>+s", lambda: None)  # no description
    manager.start()
    # spec is (id, combo, trigger, description); description falls back to combo.
    assert manager.activated_specs[0][3] == "<alt>+s"


def test_start_with_no_bindings_does_not_open_a_session():
    manager = _StubManager()
    manager.start()
    assert manager.activated_specs is None  # _activate never called
    assert manager.deactivate_calls == 1  # but a prior session would be dropped


def test_dispatch_routes_activated_id_to_its_callback():
    fired = []
    manager = _StubManager()
    manager.register("<cmd>+<shift>+a", lambda: fired.append("smart"))
    manager.start()
    manager._dispatch("sq_cmd_shift_a")
    assert fired == ["smart"]


def test_dispatch_ignores_unknown_id():
    fired = []
    manager = _StubManager()
    manager.register("<cmd>+a", lambda: fired.append(True))
    manager.start()
    manager._dispatch("sq_some_other_shortcut")  # not ours: no-op
    assert fired == []


def test_rebind_drops_the_old_id():
    # Regression: a stale id from a previous bind must not fire after the user
    # changes the hotkey in Settings (which re-registers and re-starts).
    fired = []
    manager = _StubManager()
    manager.register("<cmd>+a", lambda: fired.append("old"))
    manager.start()
    manager.clear()
    manager.register("<cmd>+s", lambda: fired.append("new"))
    manager.start()
    manager._dispatch("sq_cmd_a")  # old id gone
    manager._dispatch("sq_cmd_s")  # new id fires
    assert fired == ["new"]


def test_on_activated_dispatches_for_our_session():
    fired = []
    manager = _StubManager()
    manager.register("<cmd>+a", lambda: fired.append(True))
    manager.start()  # sets _session_handle on the stub
    manager._on_activated(_Message(manager._session_handle, "sq_cmd_a", 0, {}))
    assert fired == [True]


def test_on_activated_ignores_other_sessions():
    fired = []
    manager = _StubManager()
    manager.register("<cmd>+a", lambda: fired.append(True))
    manager.start()
    manager._on_activated(_Message("/some/other/session", "sq_cmd_a", 0, {}))
    assert fired == []


def test_on_activated_ignores_signals_after_teardown():
    # After stop() there is no open session, so a late/stray Activated (a
    # disconnect race, or the portal signalling the session we just closed) must
    # not fire a capture even though the id map is still populated.
    fired = []
    manager = _StubManager()
    manager.register("<cmd>+a", lambda: fired.append(True))
    manager.start()
    handle = manager._session_handle
    manager.stop()  # _session_handle -> None
    manager._on_activated(_Message(handle, "sq_cmd_a", 0, {}))
    assert fired == []


def test_on_activated_ignores_malformed_signal():
    manager = _StubManager()
    manager.register("<cmd>+a", lambda: None)
    manager.start()
    manager._on_activated(_Message("/session/1"))  # missing the shortcut id: no crash


def test_stop_tears_down_and_clears_the_session():
    manager = _StubManager()
    manager.register("<cmd>+a", lambda: None)
    manager.start()
    manager.stop()
    assert manager.deactivate_calls >= 1
    assert manager._session_handle is None
    assert manager._ids == {}  # the id map must not outlive the session


def test_start_propagates_hotkey_unavailable():
    # When the portal is missing, _activate raises HotkeyUnavailable; the app
    # layer catches it to fall back to the tray menu, so it must surface as-is.
    class _NoPortal(WaylandHotkeyManager):
        def _activate(self, specs):
            raise HotkeyUnavailable("the GlobalShortcuts portal is unavailable")

    manager = _NoPortal()
    manager.register("<cmd>+a", lambda: None)
    with pytest.raises(HotkeyUnavailable):
        manager.start()


def test_globalshortcuts_available_returns_bool_without_raising():
    assert isinstance(wayland.globalshortcuts_available(), bool)


def test_request_handle_path_derives_per_portal_spec():
    assert (
        wayland._request_handle_path(":1.42", "shotquill_abc")
        == "/org/freedesktop/portal/desktop/request/1_42/shotquill_abc"
    )
    assert (
        wayland._request_handle_path("1.512", "token")
        == "/org/freedesktop/portal/desktop/request/1_512/token"
    )


def test_call_request_subscribes_to_predicted_response_before_call(monkeypatch):
    manager = WaylandHotkeyManager()
    events = []
    callbacks = []

    class _Timer:
        def setSingleShot(self, value):
            pass

        @property
        def timeout(self):
            return self

        def connect(self, callback):
            pass

        def start(self, timeout):
            pass

    class _Loop:
        def exec(self):
            # A real test bus would deliver the signal asynchronously. Here the
            # point is ordering: the predicted-path connect must already exist
            # before the portal method call is made, or a fast Response can be
            # missed in production.
            events.append(("loop",))
            callbacks[-1](_Message(0, {}))

        def quit(self):
            pass

    class _Reply:
        def arguments(self):
            return ["/org/freedesktop/portal/desktop/request/1_42/token"]

        def errorMessage(self):
            return ""

    class _Bus:
        def baseService(self):
            return ":1.42"

        def connect(self, service, path, iface, signal, callback):
            events.append(("connect", path))
            callbacks.append(callback)
            return True

        def disconnect(self, service, path, iface, signal, callback):
            events.append(("disconnect", path))

    class _Iface:
        def call(self, method, *args):
            events.append(("call", method))
            return _Reply()

    monkeypatch.setattr(wayland, "_PORTAL_TIMEOUT_MS", 1)
    monkeypatch.setattr("PySide6.QtCore.QTimer", _Timer)
    monkeypatch.setattr("PySide6.QtCore.QEventLoop", _Loop)
    result = manager._call_request(
        _Bus(), _Iface(), "BindShortcuts", "token", "session", [], "", {}
    )
    predicted = "/org/freedesktop/portal/desktop/request/1_42/token"
    assert result == {}
    assert events[0] == ("connect", predicted)
    assert events[1] == ("call", "BindShortcuts")


def test_get_manager_routes_wayland_to_portal_backend(monkeypatch):
    from shotquill import hotkeys

    monkeypatch.setattr(hotkeys.sys, "platform", "linux")
    monkeypatch.setattr(hotkeys, "_is_wayland_session", lambda: True)
    assert isinstance(hotkeys.get_manager(), WaylandHotkeyManager)


def test_get_manager_routes_x11_to_pynput_backend(monkeypatch):
    from shotquill import hotkeys
    from shotquill.hotkeys.linux import LinuxHotkeyManager

    monkeypatch.setattr(hotkeys.sys, "platform", "linux")
    monkeypatch.setattr(hotkeys, "_is_wayland_session", lambda: False)
    assert isinstance(hotkeys.get_manager(), LinuxHotkeyManager)


def test_doctor_hotkeys_check_reports_reachable_portal(monkeypatch):
    from shotquill import headless

    monkeypatch.setattr(headless.sys, "platform", "linux")
    monkeypatch.setattr(headless, "_is_wayland_session", lambda: True)
    monkeypatch.setattr(wayland, "globalshortcuts_available", lambda: True)
    check = headless._check_hotkeys()
    assert check["capability"] == "hotkeys"
    assert check["available"] is True
    assert "globalshortcuts" in check["detail"].lower()


def test_doctor_hotkeys_check_flags_missing_portal(monkeypatch):
    from shotquill import headless

    monkeypatch.setattr(headless.sys, "platform", "linux")
    monkeypatch.setattr(headless, "_is_wayland_session", lambda: True)
    monkeypatch.setattr(wayland, "globalshortcuts_available", lambda: False)
    check = headless._check_hotkeys()
    assert check["available"] is False
    assert "install xdg-desktop-portal" in check["detail"]  # actionable, not a bare class name


def test_doctor_hotkeys_check_reports_x11_pynput(monkeypatch):
    from shotquill import headless

    monkeypatch.setattr(headless.sys, "platform", "linux")
    monkeypatch.setattr(headless, "_is_wayland_session", lambda: False)
    check = headless._check_hotkeys()
    assert check["available"] is True
    assert "X11" in check["detail"]
