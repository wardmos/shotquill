# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the Windows hotkey manager's bookkeeping and match logic.

The real ``pynput`` listener is never started — ``keyboard.Listener`` is replaced
with a recording fake so we can drive the Win32 event filter directly. The live
Win32 listener still needs a real Windows session to exercise.
"""

import threading

import pytest

from shotquill.hotkeys import windows

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101


class _FakeListener:
    instances: list = []

    def __init__(self, on_press=None, on_release=None, **kwargs):
        self.on_press = on_press
        self.on_release = on_release
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.suppressed = 0
        _FakeListener.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def suppress_event(self):
        self.suppressed += 1


@pytest.fixture
def fake_listener(monkeypatch):
    _FakeListener.instances = []
    monkeypatch.setattr(windows.keyboard, "Listener", _FakeListener)
    return _FakeListener


def _data(vk: int):
    return type("Data", (), {"vkCode": vk})()


def _press(manager: windows.WindowsHotkeyManager, vk: int) -> bool:
    return manager._event_filter(WM_KEYDOWN, _data(vk))


def _release(manager: windows.WindowsHotkeyManager, vk: int) -> bool:
    return manager._event_filter(WM_KEYUP, _data(vk))


def _sync_callbacks(manager: windows.WindowsHotkeyManager) -> None:
    manager._enqueue_callback = lambda callback: callback()


def test_vk_table_uses_windows_virtual_key_codes():
    # Letters/digits are their uppercase ASCII code point; F-keys are 0x70+.
    assert windows._WIN_VK["a"] == 0x41
    assert windows._WIN_VK["z"] == 0x5A
    assert windows._WIN_VK["0"] == 0x30
    assert windows._WIN_VK["9"] == 0x39
    assert windows._WIN_VK["f1"] == 0x70
    assert windows._WIN_VK["f12"] == 0x7B
    assert windows._WIN_VK["-"] == 0xBD


def test_register_and_unregister(fake_listener):
    manager = windows.WindowsHotkeyManager()
    cb = lambda: None  # noqa: E731
    manager.register("<ctrl>+<shift>+s", cb)
    manager.register("<ctrl>+<shift>+a", cb)
    manager.unregister("<ctrl>+<shift>+a")
    manager.start()
    assert set(manager._bindings) == {"<ctrl>+<shift>+s"}
    assert fake_listener.instances[-1].started is True


def test_register_accepts_description_kwarg(fake_listener):
    manager = windows.WindowsHotkeyManager()
    manager.register("<ctrl>+<shift>+s", lambda: None, description="Smart capture")
    assert set(manager._bindings) == {"<ctrl>+<shift>+s"}


def test_start_with_no_bindings_does_nothing(fake_listener):
    manager = windows.WindowsHotkeyManager()
    manager.start()
    assert fake_listener.instances == []


def test_start_needs_no_permission(fake_listener):
    manager = windows.WindowsHotkeyManager()
    manager.register("<ctrl>+<shift>+s", lambda: None)
    manager.start()
    assert fake_listener.instances[-1].started is True


def test_ctrl_combo_matches_by_vk_before_pynput_char_translation(fake_listener):
    fired = []
    manager = windows.WindowsHotkeyManager()
    _sync_callbacks(manager)
    manager.register("<ctrl>+a", lambda: fired.append(True))
    manager.start()

    assert _press(manager, 0x11) is True
    assert _press(manager, 0x41) is False

    assert fired == [True]


def test_function_key_combo_matches_by_vk(fake_listener):
    fired = []
    manager = windows.WindowsHotkeyManager()
    _sync_callbacks(manager)
    manager.register("<ctrl>+f5", lambda: fired.append(True))
    manager.start()

    _press(manager, 0x11)
    assert _press(manager, 0x74) is False

    assert fired == [True]


def test_known_punctuation_combo_matches_by_vk(fake_listener):
    fired = []
    manager = windows.WindowsHotkeyManager()
    _sync_callbacks(manager)
    manager.register("<ctrl>+-", lambda: fired.append(True))
    manager.start()

    _press(manager, 0x11)
    assert _press(manager, 0xBD) is False

    assert fired == [True]


def test_unknown_char_only_combo_is_not_compiled_or_fired(fake_listener):
    fired = []
    manager = windows.WindowsHotkeyManager()
    _sync_callbacks(manager)
    manager.register("<ctrl>+§", lambda: fired.append(True))
    manager.start()

    _press(manager, 0x11)
    assert _press(manager, 0xDF) is True

    assert manager._compiled == []
    assert fired == []
    assert fake_listener.instances[-1].suppressed == 0


def test_matching_win32_hook_event_is_suppressed(fake_listener):
    fired = []
    manager = windows.WindowsHotkeyManager()
    _sync_callbacks(manager)
    manager.register("<ctrl>+a", lambda: fired.append(True))
    manager.start()
    listener = fake_listener.instances[-1]

    assert _press(manager, 0x11) is True
    assert _press(manager, 0x41) is False

    assert fired == [True]
    assert listener.suppressed == 1


def test_win32_hook_enqueues_callback_after_suppression(fake_listener):
    fired = []
    called = threading.Event()
    manager = windows.WindowsHotkeyManager()

    def callback():
        fired.append(True)
        called.set()

    manager.register("<ctrl>+a", callback)
    manager.start()
    listener = fake_listener.instances[-1]

    _press(manager, 0x11)
    assert _press(manager, 0x41) is False

    assert listener.suppressed == 1
    assert called.wait(1)
    assert fired == [True]


def test_callback_does_not_fire_when_teardown_prevents_suppression(fake_listener):
    fired = []
    manager = windows.WindowsHotkeyManager()
    _sync_callbacks(manager)
    manager.register("<ctrl>+a", lambda: fired.append(True))
    manager.start()

    _press(manager, 0x11)
    manager._listener = None

    assert _press(manager, 0x41) is True
    assert fired == []


def test_suppressed_win32_key_release_is_suppressed(fake_listener):
    manager = windows.WindowsHotkeyManager()
    _sync_callbacks(manager)
    manager.register("<ctrl>+a", lambda: None)
    manager.start()
    listener = fake_listener.instances[-1]

    _press(manager, 0x11)
    _press(manager, 0x41)

    assert _release(manager, 0x41) is False
    assert listener.suppressed == 2


def test_super_modifier_maps_to_cmd(fake_listener):
    fired = []
    manager = windows.WindowsHotkeyManager()
    _sync_callbacks(manager)
    manager.register("<cmd>+a", lambda: fired.append(True))
    manager.start()

    _press(manager, 0x5B)
    assert _press(manager, 0x41) is False

    assert fired == [True]


def test_autorepeat_fires_once(fake_listener):
    fired = []
    manager = windows.WindowsHotkeyManager()
    _sync_callbacks(manager)
    manager.register("<ctrl>+a", lambda: fired.append(True))
    manager.start()

    _press(manager, 0x11)
    _press(manager, 0x41)
    _press(manager, 0x41)

    assert fired == [True]


def test_release_allows_refire(fake_listener):
    fired = []
    manager = windows.WindowsHotkeyManager()
    _sync_callbacks(manager)
    manager.register("<ctrl>+a", lambda: fired.append(True))
    manager.start()

    _press(manager, 0x11)
    _press(manager, 0x41)
    _release(manager, 0x41)
    _press(manager, 0x41)

    assert fired == [True, True]


def test_wrong_modifier_does_not_fire(fake_listener):
    fired = []
    manager = windows.WindowsHotkeyManager()
    _sync_callbacks(manager)
    manager.register("<ctrl>+a", lambda: fired.append(True))
    manager.start()

    _press(manager, 0x12)
    assert _press(manager, 0x41) is True

    assert fired == []


def test_extra_modifier_does_not_fire(fake_listener):
    fired = []
    manager = windows.WindowsHotkeyManager()
    _sync_callbacks(manager)
    manager.register("<ctrl>+a", lambda: fired.append(True))
    manager.start()

    _press(manager, 0x11)
    _press(manager, 0x10)
    assert _press(manager, 0x41) is True

    assert fired == []


def test_no_modifier_does_not_fire(fake_listener):
    fired = []
    manager = windows.WindowsHotkeyManager()
    _sync_callbacks(manager)
    manager.register("<ctrl>+a", lambda: fired.append(True))
    manager.start()

    assert _press(manager, 0x41) is True

    assert fired == []


def test_rebind_takes_effect_without_restart(fake_listener):
    fired = []
    manager = windows.WindowsHotkeyManager()
    _sync_callbacks(manager)
    manager.register("<ctrl>+a", lambda: fired.append("a"))
    manager.start()
    manager.clear()
    manager.register("<ctrl>+s", lambda: fired.append("s"))
    manager.start()

    assert len(fake_listener.instances) == 1
    _press(manager, 0x11)
    _press(manager, 0x41)
    _press(manager, 0x53)

    assert fired == ["s"]


def test_clear_drops_held_and_suppressed_state(fake_listener):
    fired = []
    manager = windows.WindowsHotkeyManager()
    _sync_callbacks(manager)
    manager.register("<ctrl>+a", lambda: fired.append("a"))
    manager.start()

    _press(manager, 0x11)
    _press(manager, 0x41)
    manager.clear()
    manager.register("<ctrl>+s", lambda: fired.append("s"))
    manager.start()

    assert _release(manager, 0x41) is True
    assert _press(manager, 0x53) is True
    assert fired == ["a"]


def test_disabling_all_hotkeys_unbinds_but_keeps_listener(fake_listener):
    fired = []
    manager = windows.WindowsHotkeyManager()
    _sync_callbacks(manager)
    manager.register("<ctrl>+a", lambda: fired.append(True))
    manager.start()
    listener = fake_listener.instances[-1]
    manager.clear()
    manager.start()

    assert listener.stopped is False
    _press(manager, 0x11)
    _press(manager, 0x41)
    assert fired == []


def test_stop_clears_modifier_state(fake_listener):
    fired = []
    manager = windows.WindowsHotkeyManager()
    _sync_callbacks(manager)
    manager.register("<ctrl>+a", lambda: fired.append(True))
    manager.start()
    _press(manager, 0x11)
    manager.stop()
    manager.start()
    _press(manager, 0x41)
    assert fired == []


def test_stop_is_idempotent(fake_listener):
    manager = windows.WindowsHotkeyManager()
    manager.register("<ctrl>+a", lambda: None)
    manager.start()
    manager.stop()
    manager.stop()
    assert fake_listener.instances[-1].stopped is True


def test_factory_routes_to_windows(monkeypatch):
    from shotquill import hotkeys

    monkeypatch.setattr(hotkeys.sys, "platform", "win32")
    assert isinstance(hotkeys.get_manager(), windows.WindowsHotkeyManager)
