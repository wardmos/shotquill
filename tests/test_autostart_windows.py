# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the Windows ``Run``-key autostart manager and platform factories.

``winreg`` is Windows-only stdlib, so the registry methods are exercised against
an in-memory fake injected into ``sys.modules`` — the pure command-line helpers
need no faking and run on any platform.
"""

from __future__ import annotations

import sys

import pytest

from shotquill import autostart
from shotquill.autostart import windows as autostart_windows

# --- pure helpers (no registry) ---------------------------------------------


def test_build_run_command_quotes_paths_with_spaces():
    # A Run value is one command-line string CreateProcess parses back, so an
    # interpreter under "Program Files" must round-trip to a single argv entry.
    command = autostart_windows.build_run_command([r"C:\Program Files\ShotQuill\shotquill.exe"])
    assert command == r'"C:\Program Files\ShotQuill\shotquill.exe"'


def test_build_run_command_renders_all_arguments():
    command = autostart_windows.build_run_command([r"C:\Py\python.exe", "-m", "shotquill"])
    assert command == r"C:\Py\python.exe -m shotquill"


def test_launch_arguments_dev_runs_module(monkeypatch):
    monkeypatch.setattr(autostart_windows.sys, "frozen", False, raising=False)
    args = autostart_windows.launch_arguments()
    assert args[1:] == ["-m", "shotquill"]
    assert args[0]  # interpreter path is non-empty


def test_launch_arguments_frozen_uses_executable(monkeypatch):
    monkeypatch.setattr(autostart_windows.sys, "frozen", True, raising=False)
    monkeypatch.setattr(autostart_windows.sys, "executable", r"C:\Apps\ShotQuill\shotquill.exe")
    assert autostart_windows.launch_arguments() == [r"C:\Apps\ShotQuill\shotquill.exe"]


# --- registry methods against a fake winreg ----------------------------------


class _FakeKey:
    """Context-manager handle over the in-memory value store for one path."""

    def __init__(self, store: dict, writable: bool):
        self._store = store
        self._writable = writable

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeWinreg:
    """Minimal in-memory ``winreg`` standing in for the real Windows registry."""

    HKEY_CURRENT_USER = object()
    KEY_READ = 1
    KEY_WRITE = 2
    REG_SZ = 1

    def __init__(self):
        # path -> {value_name: (data, type)}; the Run key starts absent so the
        # "not enabled" read path (OpenKey raising) is covered too.
        self._keys: dict[str, dict] = {}

    def CreateKeyEx(self, root, path, reserved, access):
        assert root is self.HKEY_CURRENT_USER
        return _FakeKey(self._keys.setdefault(path, {}), writable=True)

    def OpenKey(self, root, path, reserved, access):
        assert root is self.HKEY_CURRENT_USER
        if path not in self._keys:
            raise FileNotFoundError(path)
        return _FakeKey(self._keys[path], writable=False)

    def QueryValueEx(self, key, name):
        if name not in key._store:
            raise FileNotFoundError(name)
        return key._store[name]

    def SetValueEx(self, key, name, reserved, type_, value):
        key._store[name] = (value, type_)

    def DeleteValue(self, key, name):
        if name not in key._store:
            raise FileNotFoundError(name)
        del key._store[name]


@pytest.fixture
def fake_winreg(monkeypatch):
    fake = _FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", fake)
    return fake


def test_enable_disable_roundtrip(fake_winreg):
    mgr = autostart_windows.WindowsAutostartManager()
    assert mgr.is_enabled() is False  # Run key absent entirely

    mgr.enable()
    assert mgr.is_enabled() is True
    store = fake_winreg._keys[autostart_windows.RUN_KEY_PATH]
    data, type_ = store[autostart_windows.RUN_VALUE_NAME]
    assert type_ == _FakeWinreg.REG_SZ
    assert "shotquill" in data.lower()

    mgr.enable()  # idempotent: overwrites the same value
    assert mgr.is_enabled() is True

    mgr.disable()
    assert mgr.is_enabled() is False
    mgr.disable()  # idempotent: value already gone, must not raise


def test_is_enabled_false_when_key_exists_but_value_absent(fake_winreg):
    # Another app may have created the Run key; our value still being absent
    # must read as "not enabled", not crash.
    fake_winreg._keys[autostart_windows.RUN_KEY_PATH] = {"OtherApp": ("x", 1)}
    mgr = autostart_windows.WindowsAutostartManager()
    assert mgr.is_enabled() is False


def test_disable_leaves_other_values_intact(fake_winreg):
    fake_winreg._keys[autostart_windows.RUN_KEY_PATH] = {"OtherApp": ("x", 1)}
    mgr = autostart_windows.WindowsAutostartManager()
    mgr.enable()
    mgr.disable()
    assert "OtherApp" in fake_winreg._keys[autostart_windows.RUN_KEY_PATH]


def test_set_enabled_toggles(fake_winreg):
    mgr = autostart_windows.WindowsAutostartManager()
    mgr.set_enabled(True)
    assert mgr.is_enabled() is True
    mgr.set_enabled(False)
    assert mgr.is_enabled() is False


# --- factory routing ----------------------------------------------------------


def test_autostart_factory_routes_to_windows(monkeypatch):
    monkeypatch.setattr(autostart.sys, "platform", "win32")
    assert isinstance(autostart.get_manager(), autostart_windows.WindowsAutostartManager)
