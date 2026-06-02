# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the macOS hotkey manager's binding bookkeeping.

The real ``pynput`` listener is never started — ``GlobalHotKeys`` is replaced with
a recording fake so we can assert what gets registered without grabbing the
keyboard or needing the Input Monitoring permission.
"""

import pytest

from shotquill.hotkeys import macos


class _FakeListener:
    instances: list = []

    def __init__(self, mapping):
        self.mapping = dict(mapping)
        self.started = False
        self.stopped = False
        _FakeListener.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


@pytest.fixture
def fake_listener(monkeypatch):
    _FakeListener.instances = []
    monkeypatch.setattr(macos.keyboard, "GlobalHotKeys", _FakeListener)
    return _FakeListener


def test_register_and_unregister(fake_listener):
    manager = macos.MacHotkeyManager()
    cb = lambda: None  # noqa: E731
    manager.register("<alt>+a", cb)
    manager.register("<alt>+s", cb)
    manager.unregister("<alt>+a")
    manager.start()
    assert set(fake_listener.instances[-1].mapping) == {"<alt>+s"}


def test_start_with_no_bindings_does_nothing(fake_listener):
    manager = macos.MacHotkeyManager()
    manager.start()
    assert fake_listener.instances == []


def test_start_stops_previous_listener(fake_listener):
    manager = macos.MacHotkeyManager()
    manager.register("<alt>+a", lambda: None)
    manager.start()
    first = fake_listener.instances[-1]
    manager.start()  # re-applying settings starts a fresh listener
    assert first.stopped is True
    assert len(fake_listener.instances) == 2
    assert fake_listener.instances[-1].started is True


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
