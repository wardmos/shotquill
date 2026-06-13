# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the Windows hotkey manager's bookkeeping and match logic.

The real ``pynput`` listener is never started — ``keyboard.Listener`` is replaced
with a recording fake so we can drive ``on_press``/``on_release`` directly. The
live Win32 listener still needs a real Windows session to exercise.
"""

import pytest
from pynput.keyboard import Key

from shotquill.hotkeys import windows


class _FakeListener:
    instances: list = []

    def __init__(self, on_press=None, on_release=None):
        self.on_press = on_press
        self.on_release = on_release
        self.started = False
        self.stopped = False
        _FakeListener.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class _FakeKey:
    """Stand-in for a pynput KeyCode: a virtual-key ``vk`` plus produced ``char``."""

    def __init__(self, vk=None, char=None):
        self.vk = vk
        self.char = char


@pytest.fixture
def fake_listener(monkeypatch):
    _FakeListener.instances = []
    monkeypatch.setattr(windows.keyboard, "Listener", _FakeListener)
    return _FakeListener


def test_vk_table_uses_windows_virtual_key_codes():
    # Letters/digits are their uppercase ASCII code point; F-keys are 0x70+.
    assert windows._WIN_VK["a"] == 0x41
    assert windows._WIN_VK["z"] == 0x5A
    assert windows._WIN_VK["0"] == 0x30
    assert windows._WIN_VK["9"] == 0x39
    assert windows._WIN_VK["f1"] == 0x70
    assert windows._WIN_VK["f12"] == 0x7B


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
    # The app layer calls register(combo, cb, description=label) for every
    # backend (the Wayland portal uses it); the pynput backends accept and
    # ignore it. A missing param would TypeError at GUI startup on Windows.
    manager = windows.WindowsHotkeyManager()
    manager.register("<ctrl>+<shift>+s", lambda: None, description="Smart capture")
    assert set(manager._bindings) == {"<ctrl>+<shift>+s"}


def test_start_with_no_bindings_does_nothing(fake_listener):
    manager = windows.WindowsHotkeyManager()
    manager.start()
    assert fake_listener.instances == []


def test_start_needs_no_permission(fake_listener):
    # Unlike macOS Input Monitoring, a Win32 listener starts without a grant.
    manager = windows.WindowsHotkeyManager()
    manager.register("<ctrl>+<shift>+s", lambda: None)
    manager.start()  # must not raise PermissionError
    assert fake_listener.instances[-1].started is True


def test_ctrl_combo_matches_by_vk_when_char_is_control_code(fake_listener):
    # On Windows, Ctrl+A makes ToUnicode emit "\x01"; matching the vk (0x41)
    # keeps the binding firing where character matching would miss.
    fired = []
    manager = windows.WindowsHotkeyManager()
    manager.register("<ctrl>+a", lambda: fired.append(True))
    manager.start()
    manager._on_press(Key.ctrl)
    manager._on_press(_FakeKey(vk=0x41, char="\x01"))
    assert fired == [True]


def test_function_key_combo_matches_by_vk(fake_listener):
    fired = []
    manager = windows.WindowsHotkeyManager()
    manager.register("<ctrl>+f5", lambda: fired.append(True))
    manager.start()
    manager._on_press(Key.ctrl)
    # Function keys arrive as Key members; vk resolves through the wrapped value.
    manager._on_press(_FakeKey(vk=0x74))  # VK_F5
    assert fired == [True]


def test_char_fallback_for_keys_outside_vk_table(fake_listener):
    # A key with no vk entry (e.g. a punctuation key) falls back to char.
    fired = []
    manager = windows.WindowsHotkeyManager()
    manager.register("<ctrl>+-", lambda: fired.append(True))
    manager.start()
    manager._on_press(Key.ctrl)
    manager._on_press(_FakeKey(vk=None, char="-"))
    assert fired == [True]


def test_super_modifier_maps_to_cmd(fake_listener):
    fired = []
    manager = windows.WindowsHotkeyManager()
    manager.register("<cmd>+a", lambda: fired.append(True))  # cmd == Win key
    manager.start()
    manager._on_press(Key.cmd)
    manager._on_press(_FakeKey(vk=0x41, char="a"))
    assert fired == [True]


def test_autorepeat_fires_once(fake_listener):
    fired = []
    manager = windows.WindowsHotkeyManager()
    manager.register("<ctrl>+a", lambda: fired.append(True))
    manager.start()
    manager._on_press(Key.ctrl)
    manager._on_press(_FakeKey(vk=0x41, char="\x01"))
    manager._on_press(_FakeKey(vk=0x41, char="\x01"))  # held key auto-repeats
    assert fired == [True]


def test_release_allows_refire(fake_listener):
    fired = []
    manager = windows.WindowsHotkeyManager()
    manager.register("<ctrl>+a", lambda: fired.append(True))
    manager.start()
    manager._on_press(Key.ctrl)
    manager._on_press(_FakeKey(vk=0x41, char="\x01"))
    manager._on_release(_FakeKey(vk=0x41, char="\x01"))
    manager._on_press(_FakeKey(vk=0x41, char="\x01"))
    assert fired == [True, True]


def test_wrong_modifier_does_not_fire(fake_listener):
    fired = []
    manager = windows.WindowsHotkeyManager()
    manager.register("<ctrl>+a", lambda: fired.append(True))
    manager.start()
    manager._on_press(Key.alt)  # Alt, not Ctrl
    manager._on_press(_FakeKey(vk=0x41, char="a"))
    assert fired == []


def test_extra_modifier_does_not_fire(fake_listener):
    fired = []
    manager = windows.WindowsHotkeyManager()
    manager.register("<ctrl>+a", lambda: fired.append(True))
    manager.start()
    manager._on_press(Key.ctrl)
    manager._on_press(Key.shift)  # Ctrl+Shift+A != Ctrl+A
    manager._on_press(_FakeKey(vk=0x41, char="\x01"))
    assert fired == []


def test_no_modifier_does_not_fire(fake_listener):
    fired = []
    manager = windows.WindowsHotkeyManager()
    manager.register("<ctrl>+a", lambda: fired.append(True))
    manager.start()
    manager._on_press(_FakeKey(vk=0x41, char="a"))  # bare 'a'
    assert fired == []


def test_rebind_takes_effect_without_restart(fake_listener):
    fired = []
    manager = windows.WindowsHotkeyManager()
    manager.register("<ctrl>+a", lambda: fired.append("a"))
    manager.start()
    manager.clear()
    manager.register("<ctrl>+s", lambda: fired.append("s"))
    manager.start()
    assert len(fake_listener.instances) == 1  # listener reused, not restarted
    manager._on_press(Key.ctrl)
    manager._on_press(_FakeKey(vk=0x41, char="\x01"))  # old binding gone
    manager._on_press(_FakeKey(vk=0x53, char="\x13"))  # new <ctrl>+s fires
    assert fired == ["s"]


def test_disabling_all_hotkeys_unbinds_but_keeps_listener(fake_listener):
    fired = []
    manager = windows.WindowsHotkeyManager()
    manager.register("<ctrl>+a", lambda: fired.append(True))
    manager.start()
    listener = fake_listener.instances[-1]
    manager.clear()
    manager.start()  # all hotkeys disabled in Settings
    assert listener.stopped is False
    manager._on_press(Key.ctrl)
    manager._on_press(_FakeKey(vk=0x41, char="\x01"))
    assert fired == []


def test_stop_clears_modifier_state(fake_listener):
    fired = []
    manager = windows.WindowsHotkeyManager()
    manager.register("<ctrl>+a", lambda: fired.append(True))
    manager.start()
    manager._on_press(Key.ctrl)  # hold Ctrl, never release
    manager.stop()
    manager.start()
    manager._on_press(_FakeKey(vk=0x41, char="a"))  # no live Ctrl
    assert fired == []


def test_stop_is_idempotent(fake_listener):
    manager = windows.WindowsHotkeyManager()
    manager.register("<ctrl>+a", lambda: None)
    manager.start()
    manager.stop()
    manager.stop()  # second stop is a no-op, must not raise
    assert fake_listener.instances[-1].stopped is True


def test_factory_routes_to_windows(monkeypatch):
    from shotquill import hotkeys

    monkeypatch.setattr(hotkeys.sys, "platform", "win32")
    assert isinstance(hotkeys.get_manager(), windows.WindowsHotkeyManager)
