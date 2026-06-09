# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the Linux (X11) hotkey manager's bookkeeping and match logic.

The real ``pynput`` listener is never started — ``keyboard.Listener`` is replaced
with a recording fake so we can drive ``on_press``/``on_release`` directly. The
live X11 listener still needs a real X server to exercise.
"""

import pytest
from pynput.keyboard import Key

from shotquill.hotkeys import linux


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
    """Stand-in for a pynput KeyCode: the produced character for a key press."""

    def __init__(self, char=None):
        self.char = char


@pytest.fixture
def fake_listener(monkeypatch):
    _FakeListener.instances = []
    monkeypatch.setattr(linux.keyboard, "Listener", _FakeListener)
    return _FakeListener


def test_register_and_unregister(fake_listener):
    manager = linux.LinuxHotkeyManager()
    cb = lambda: None  # noqa: E731
    manager.register("<ctrl>+<shift>+s", cb)
    manager.register("<ctrl>+<shift>+a", cb)
    manager.unregister("<ctrl>+<shift>+a")
    manager.start()
    assert set(manager._bindings) == {"<ctrl>+<shift>+s"}
    assert fake_listener.instances[-1].started is True


def test_start_with_no_bindings_does_nothing(fake_listener):
    manager = linux.LinuxHotkeyManager()
    manager.start()
    assert fake_listener.instances == []


def test_start_needs_no_permission(fake_listener):
    # Unlike macOS Input Monitoring, an X11 listener starts without a grant.
    manager = linux.LinuxHotkeyManager()
    manager.register("<ctrl>+<shift>+s", lambda: None)
    manager.start()  # must not raise PermissionError
    assert fake_listener.instances[-1].started is True


def test_letter_combo_matches_by_lowercased_char(fake_listener):
    # X11 reports 'S' when Shift is held; lowercasing keeps the binding firing.
    fired = []
    manager = linux.LinuxHotkeyManager()
    manager.register("<ctrl>+<shift>+s", lambda: fired.append(True))
    manager.start()
    manager._on_press(Key.ctrl)
    manager._on_press(Key.shift)
    manager._on_press(_FakeKey(char="S"))
    assert fired == [True]


def test_function_key_combo_matches(fake_listener):
    fired = []
    manager = linux.LinuxHotkeyManager()
    manager.register("<ctrl>+f5", lambda: fired.append(True))
    manager.start()
    manager._on_press(Key.ctrl)
    manager._on_press(Key.f5)  # function keys arrive as Key members (no .char)
    assert fired == [True]


def test_super_modifier_maps_to_cmd(fake_listener):
    fired = []
    manager = linux.LinuxHotkeyManager()
    manager.register("<cmd>+a", lambda: fired.append(True))  # cmd == Super on Linux
    manager.start()
    manager._on_press(Key.cmd)
    manager._on_press(_FakeKey(char="a"))
    assert fired == [True]


def test_autorepeat_fires_once(fake_listener):
    fired = []
    manager = linux.LinuxHotkeyManager()
    manager.register("<ctrl>+a", lambda: fired.append(True))
    manager.start()
    manager._on_press(Key.ctrl)
    manager._on_press(_FakeKey(char="a"))
    manager._on_press(_FakeKey(char="a"))  # held key auto-repeats
    assert fired == [True]


def test_release_allows_refire(fake_listener):
    fired = []
    manager = linux.LinuxHotkeyManager()
    manager.register("<ctrl>+a", lambda: fired.append(True))
    manager.start()
    manager._on_press(Key.ctrl)
    manager._on_press(_FakeKey(char="a"))
    manager._on_release(_FakeKey(char="a"))
    manager._on_press(_FakeKey(char="a"))
    assert fired == [True, True]


def test_wrong_modifier_does_not_fire(fake_listener):
    fired = []
    manager = linux.LinuxHotkeyManager()
    manager.register("<ctrl>+a", lambda: fired.append(True))
    manager.start()
    manager._on_press(Key.alt)  # Alt, not Ctrl
    manager._on_press(_FakeKey(char="a"))
    assert fired == []


def test_extra_modifier_does_not_fire(fake_listener):
    fired = []
    manager = linux.LinuxHotkeyManager()
    manager.register("<ctrl>+a", lambda: fired.append(True))
    manager.start()
    manager._on_press(Key.ctrl)
    manager._on_press(Key.shift)  # Ctrl+Shift+A != Ctrl+A
    manager._on_press(_FakeKey(char="A"))
    assert fired == []


def test_no_modifier_does_not_fire(fake_listener):
    fired = []
    manager = linux.LinuxHotkeyManager()
    manager.register("<ctrl>+a", lambda: fired.append(True))
    manager.start()
    manager._on_press(_FakeKey(char="a"))  # bare 'a'
    assert fired == []


def test_rebind_takes_effect_without_restart(fake_listener):
    fired = []
    manager = linux.LinuxHotkeyManager()
    manager.register("<ctrl>+a", lambda: fired.append("a"))
    manager.start()
    manager.clear()
    manager.register("<ctrl>+s", lambda: fired.append("s"))
    manager.start()
    assert len(fake_listener.instances) == 1  # listener reused, not restarted
    manager._on_press(Key.ctrl)
    manager._on_press(_FakeKey(char="a"))  # old binding gone
    manager._on_press(_FakeKey(char="s"))  # new binding fires
    assert fired == ["s"]


def test_stop_clears_modifier_state(fake_listener):
    fired = []
    manager = linux.LinuxHotkeyManager()
    manager.register("<ctrl>+a", lambda: fired.append(True))
    manager.start()
    manager._on_press(Key.ctrl)  # hold Ctrl, never release
    manager.stop()
    manager.start()
    manager._on_press(_FakeKey(char="a"))  # no live Ctrl
    assert fired == []


def test_stop_is_idempotent(fake_listener):
    manager = linux.LinuxHotkeyManager()
    manager.register("<ctrl>+a", lambda: None)
    manager.start()
    manager.stop()
    manager.stop()  # second stop is a no-op, must not raise
    assert fake_listener.instances[-1].stopped is True
