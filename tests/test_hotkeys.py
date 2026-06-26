# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the macOS hotkey manager's binding bookkeeping and matching.

The real ``pynput`` listener is never started — ``keyboard.Listener`` is replaced
with a recording fake so we can drive ``on_press``/``on_release`` directly and
assert what fires, without grabbing the keyboard or needing Input Monitoring.
"""

import pytest
from pynput.keyboard import Key

from shotquill.hotkeys import macos


class _FakeListener:
    instances: list = []

    def __init__(self, on_press=None, on_release=None, **kwargs):
        self.on_press = on_press
        self.on_release = on_release
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        _FakeListener.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class _FakeKey:
    """Stand-in for a pynput KeyCode: a hardware ``vk`` plus produced ``char``."""

    def __init__(self, vk=None, char=None):
        self.vk = vk
        self.char = char


@pytest.fixture
def fake_listener(monkeypatch):
    _FakeListener.instances = []
    monkeypatch.setattr(macos.keyboard, "Listener", _FakeListener)
    # Decouple from the runner's TCC state: the real preflight returns False on
    # an unattended macOS CI runner (no Input Monitoring grant), which would make
    # start() raise. Tests that exercise the permission path override this.
    monkeypatch.setattr(macos, "request_input_monitoring_access", lambda: True)
    return _FakeListener


def test_register_and_unregister(fake_listener):
    manager = macos.MacHotkeyManager()
    cb = lambda: None  # noqa: E731
    manager.register("<alt>+a", cb)
    manager.register("<alt>+s", cb)
    manager.unregister("<alt>+a")
    manager.start()
    assert set(manager._bindings) == {"<alt>+s"}
    assert fake_listener.instances[-1].started is True


def test_start_with_no_bindings_does_nothing(fake_listener):
    manager = macos.MacHotkeyManager()
    manager.start()
    assert fake_listener.instances == []


def test_start_requests_input_monitoring_when_needed(fake_listener, monkeypatch):
    requested = []

    monkeypatch.setattr(macos, "has_input_monitoring_access", lambda: False)
    monkeypatch.setattr(
        macos, "request_input_monitoring_access", lambda: requested.append(True) or True
    )

    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: None)
    manager.start()

    assert requested == [True]
    assert fake_listener.instances[-1].started is True


def test_start_fails_without_input_monitoring(fake_listener, monkeypatch):
    monkeypatch.setattr(macos, "request_input_monitoring_access", lambda: False)

    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: None)

    with pytest.raises(PermissionError):
        manager.start()
    assert fake_listener.instances == []


def test_start_reuses_running_listener(fake_listener):
    # Restarting a pynput listener under a running Qt app SIGTRAPs on macOS,
    # so re-applying settings must keep the existing listener alive.
    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: None)
    manager.start()
    first = fake_listener.instances[-1]
    manager.start()  # re-applying settings hot-swaps bindings, no restart
    assert first.stopped is False
    assert fake_listener.instances == [first]


def test_rebind_takes_effect_without_restart(fake_listener):
    """Changing hotkeys in Settings swaps bindings on the live listener."""
    fired = []
    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: fired.append("a"))
    manager.start()
    manager.clear()
    manager.register("<alt>+s", lambda: fired.append("s"))
    manager.start()
    assert len(fake_listener.instances) == 1
    manager._on_press(Key.alt)
    manager._on_press(_FakeKey(vk=0, char="å"))  # old ⌥A is unbound
    manager._on_press(_FakeKey(vk=1, char="ß"))  # new ⌥S fires
    assert fired == ["s"]


def test_disabling_all_hotkeys_unbinds_but_keeps_listener(fake_listener):
    fired = []
    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: fired.append(True))
    manager.start()
    listener = fake_listener.instances[-1]
    manager.clear()
    manager.start()  # all hotkeys disabled in Settings
    assert listener.stopped is False  # stopping would make re-enable crash-prone
    manager._on_press(Key.alt)
    manager._on_press(_FakeKey(vk=0, char="å"))
    assert fired == []


def test_clear_removes_all_bindings(fake_listener):
    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: None)
    manager.clear()
    manager.start()
    assert fake_listener.instances == []


def test_stop_is_idempotent(fake_listener):
    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: None)
    manager.start()
    manager.stop()
    manager.stop()  # second stop is a no-op, must not raise
    assert fake_listener.instances[-1].stopped is True


def test_option_combo_matches_by_keycode(fake_listener):
    """⌥A reports the character 'å' but keeps key code 0 — must still fire."""
    fired = []
    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: fired.append(True))
    manager.start()
    manager._on_press(Key.alt)
    manager._on_press(_FakeKey(vk=0, char="å"))
    assert fired == [True]


def test_autorepeat_fires_once(fake_listener):
    fired = []
    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: fired.append(True))
    manager.start()
    manager._on_press(Key.alt)
    manager._on_press(_FakeKey(vk=0, char="å"))
    manager._on_press(_FakeKey(vk=0, char="å"))  # held key auto-repeats
    assert fired == [True]


def test_release_allows_refire(fake_listener):
    fired = []
    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: fired.append(True))
    manager.start()
    manager._on_press(Key.alt)
    manager._on_press(_FakeKey(vk=0, char="å"))
    manager._on_release(_FakeKey(vk=0, char="å"))
    manager._on_press(_FakeKey(vk=0, char="å"))
    assert fired == [True, True]


def test_start_always_installs_the_swallowing_intercept(fake_listener):
    # Suppression rides on the same Input Monitoring grant the listener already
    # needs — no second permission gate, so darwin_intercept is always wired up.
    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: None)
    manager.start()
    assert "darwin_intercept" in fake_listener.instances[-1].kwargs


def test_matching_macos_event_press_repeat_and_release_are_suppressed(fake_listener):
    fired = []
    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: fired.append(True))
    manager.start()
    listener = fake_listener.instances[-1]
    event = object()

    manager._on_press(Key.alt)
    manager._on_press(_FakeKey(vk=0, char="å"))

    assert fired == [True]
    assert listener.kwargs["darwin_intercept"](None, event) is None

    manager._on_press(_FakeKey(vk=0, char="å"))  # held key auto-repeats
    assert fired == [True]
    assert listener.kwargs["darwin_intercept"](None, event) is None

    manager._on_release(_FakeKey(vk=0, char="å"))
    assert listener.kwargs["darwin_intercept"](None, event) is None
    assert listener.kwargs["darwin_intercept"](None, event) is event


def test_wrong_modifier_does_not_fire(fake_listener):
    fired = []
    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: fired.append(True))
    manager.start()
    manager._on_press(Key.cmd)  # Cmd, not Option
    manager._on_press(_FakeKey(vk=0, char="a"))
    assert fired == []


def test_no_modifier_does_not_fire(fake_listener):
    fired = []
    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: fired.append(True))
    manager.start()
    manager._on_press(_FakeKey(vk=0, char="a"))  # bare 'a' keypress
    assert fired == []


def test_extra_modifier_does_not_fire(fake_listener):
    """⌥A binding must not trigger on ⌘⌥A (exact modifier set required)."""
    fired = []
    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: fired.append(True))
    manager.start()
    manager._on_press(Key.cmd)
    manager._on_press(Key.alt)
    manager._on_press(_FakeKey(vk=0, char="å"))
    assert fired == []


def test_stop_clears_modifier_state(fake_listener):
    """A modifier held when we stop must not linger into the next session."""
    fired = []
    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: fired.append(True))
    manager.start()
    manager._on_press(Key.alt)  # press Option, never release it
    manager.stop()
    manager.start()
    manager._on_press(_FakeKey(vk=0, char="a"))  # only 'a', no live Option
    assert fired == []
