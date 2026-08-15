# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the macOS Carbon hotkey manager.

The real Carbon API is replaced with a recording fake so tests can assert
registration, cleanup, and event dispatch without touching macOS frameworks.
"""

import ctypes

import pytest

from shotquill.hotkeys import macos
from shotquill.hotkeys.base import HotkeyUnavailable


class _LogRecorder:
    def __init__(self):
        self.messages = []

    def debug(self, message, *args):
        self.messages.append(message % args if args else message)


class _FakeCarbonAPI:
    def __init__(self):
        self.target = ctypes.c_void_p(9001)
        self.next_ref = 100
        self.registered = []
        self.unregistered = []
        self.installed = []
        self.removed = []
        self.register_status = 0
        self.register_statuses = []
        self.handler_status = 0
        self.event_param_status = 0

    def get_application_event_target(self):
        return self.target

    def install_event_handler(
        self,
        target,
        handler,
        count,
        event_types,
        user_data,
        out_ref,
    ):
        self.installed.append((target, handler, count, event_types, user_data))
        if self.handler_status:
            return self.handler_status
        ctypes.cast(out_ref, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.c_void_p(7001)
        return 0

    def remove_event_handler(self, ref):
        self.removed.append(ref)
        return 0

    def register_hotkey(self, vk, mods, hotkey_id, target, options, out_ref):
        self.registered.append((vk, mods, hotkey_id.signature, hotkey_id.id, target, options))
        status = self.register_statuses.pop(0) if self.register_statuses else self.register_status
        if status:
            return status
        ref = ctypes.c_void_p(self.next_ref)
        self.next_ref += 1
        ctypes.cast(out_ref, ctypes.POINTER(ctypes.c_void_p))[0] = ref
        return 0

    def unregister_hotkey(self, ref):
        self.unregistered.append(ref)
        return 0

    def get_event_parameter(
        self,
        event,
        name,
        param_type,
        actual_type,
        size,
        actual_size,
        out_data,
    ):
        assert name == macos._EVENT_PARAM_DIRECT_OBJECT
        assert param_type == macos._TYPE_EVENT_HOTKEY_ID
        assert size == ctypes.sizeof(macos._EventHotKeyID)
        if self.event_param_status:
            return self.event_param_status
        hotkey_id = int(event)
        out = ctypes.cast(out_data, ctypes.POINTER(macos._EventHotKeyID))
        out.contents.signature = macos._SIGNATURE
        out.contents.id = hotkey_id
        return 0


@pytest.fixture
def carbon_api(monkeypatch):
    api = _FakeCarbonAPI()
    monkeypatch.setattr(macos, "_load_carbon_api", lambda: api)
    return api


def _refs(values):
    return [ref.value for ref in values]


def test_registers_capture_combos_with_carbon_keycodes(carbon_api):
    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: None)
    manager.register("<cmd>+<shift>+f5", lambda: None)

    manager.start()

    assert carbon_api.installed
    assert carbon_api.registered == [
        (
            0,
            1 << 11,
            macos._SIGNATURE,
            1,
            carbon_api.target,
            0,
        ),
        (
            96,
            (1 << 8) | (1 << 9),
            macos._SIGNATURE,
            2,
            carbon_api.target,
            0,
        ),
    ]


def test_start_with_no_bindings_does_not_load_carbon(monkeypatch):
    loaded = []
    monkeypatch.setattr(macos, "_load_carbon_api", lambda: loaded.append(True))

    manager = macos.MacHotkeyManager()
    manager.start()

    assert loaded == []


def test_unsupported_key_reports_unavailable(carbon_api):
    manager = macos.MacHotkeyManager()
    manager.register("<alt>+space", lambda: None)

    with pytest.raises(HotkeyUnavailable, match="unsupported macOS hotkey key"):
        manager.start()
    assert carbon_api.registered == []


def test_event_handler_dispatches_registered_callback(carbon_api):
    fired = []
    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: fired.append("a"))
    manager.register("<alt>+s", lambda: fired.append("s"))
    manager.start()

    assert manager._handle_event(None, 2, None) == 0

    assert fired == ["s"]


def test_event_handler_ignores_unknown_hotkey_id(carbon_api):
    fired = []
    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: fired.append(True))
    manager.start()

    assert manager._handle_event(None, 99, None) == 0

    assert fired == []


def test_event_handler_returns_parameter_error(carbon_api):
    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: None)
    manager.start()
    carbon_api.event_param_status = -50

    assert manager._handle_event(None, 1, None) == -50


def test_debug_log_records_registration_and_dispatch(carbon_api, monkeypatch):
    log = _LogRecorder()
    fired = []
    monkeypatch.setattr(macos, "_LOG", log)
    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: fired.append(True))

    manager.start()
    manager._handle_event(None, 1, None)

    assert fired == [True]
    assert "carbon hotkeys start bindings=1" in log.messages
    assert "carbon api loaded" in log.messages
    assert "carbon handler installed" in log.messages
    assert "carbon hotkey registered combo=<alt>+a id=1 vk=0 mods=2048" in log.messages
    assert "carbon hotkeys active registered=1" in log.messages
    assert "carbon event dispatch combo=<alt>+a id=1" in log.messages


def test_debug_log_records_registration_failure(carbon_api, monkeypatch):
    log = _LogRecorder()
    carbon_api.register_status = -9878
    monkeypatch.setattr(macos, "_LOG", log)
    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: None)

    with pytest.raises(HotkeyUnavailable):
        manager.start()

    assert any("carbon hotkey register_failed combo=<alt>+a" in msg for msg in log.messages)
    assert "carbon hotkeys start rollback registered=0" in log.messages


def test_rebind_unregisters_old_hotkeys_and_keeps_handler(carbon_api):
    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: None)
    manager.start()
    first_handler = manager._handler_ref

    manager.clear()
    manager.register("<alt>+s", lambda: None)
    manager.start()

    assert first_handler == manager._handler_ref
    assert _refs(carbon_api.unregistered) == [100]
    assert [call[0] for call in carbon_api.registered] == [0, 1]
    assert len(carbon_api.installed) == 1


def test_unregister_removes_live_combo(carbon_api):
    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: None)
    manager.register("<alt>+s", lambda: None)
    manager.start()

    manager.unregister("<alt>+a")

    assert _refs(carbon_api.unregistered) == [100]
    assert set(manager._registered) == {2}


def test_stop_unregisters_hotkeys_and_removes_handler(carbon_api):
    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: None)
    manager.register("<alt>+s", lambda: None)
    manager.start()

    manager.stop()

    assert _refs(carbon_api.unregistered) == [100, 101]
    assert _refs(carbon_api.removed) == [7001]
    assert manager._registered == {}
    assert manager._handler_ref is None


def test_registration_failure_reports_combo(carbon_api):
    carbon_api.register_status = -9878
    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: None)

    with pytest.raises(HotkeyUnavailable, match="<alt>\\+a"):
        manager.start()


def test_registration_failure_rolls_back_previous_hotkeys(carbon_api):
    carbon_api.register_statuses = [0, -9878]
    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: None)
    manager.register("<alt>+s", lambda: None)

    with pytest.raises(HotkeyUnavailable):
        manager.start()

    assert _refs(carbon_api.unregistered) == [100]
    assert manager._registered == {}


def test_handler_install_failure_reports_unavailable(carbon_api):
    carbon_api.handler_status = -50
    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: None)

    with pytest.raises(HotkeyUnavailable, match="event handler failed"):
        manager.start()
