# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Shared pytest fixtures and headless test setup.

Three jobs:

1. *pynput fallback* — ``pynput`` needs an X server (or macOS) to import. CI has
   one (Xvfb on Linux, native on macOS), but a bare dev machine may not. When the
   real import fails we install a tiny stub so the pure-logic hotkey/app tests can
   still be collected and run; on CI the real library is used.
2. *isolated config* — a ``config`` fixture that points QSettings at a temp file so
   tests never touch (or depend on) the developer's real preferences.
3. *platform fakes* — a ``fakes`` fixture that swaps the macOS managers (screen
   capture, global hotkeys, launch-at-login) on ``shotquill.app`` for in-memory
   fakes, shared by the app-controller tests and the macOS activation tests.
"""

from __future__ import annotations

import sys
import types

import pytest

from shotquill.capture.base import CaptureResult

try:  # pragma: no cover - exercised implicitly by import success/failure
    import pynput  # noqa: F401
except Exception:  # pragma: no cover - only hit on a headless dev machine
    _pynput = types.ModuleType("pynput")
    _keyboard = types.ModuleType("pynput.keyboard")

    class _StubKey:
        alt = object()
        alt_l = object()
        alt_r = object()
        cmd = object()
        cmd_l = object()
        cmd_r = object()
        ctrl = object()
        ctrl_l = object()
        ctrl_r = object()
        shift = object()
        shift_l = object()
        shift_r = object()

    class _StubListener:  # minimal stand-in; real behaviour is monkeypatched per-test
        def __init__(self, on_press=None, on_release=None) -> None:
            self.on_press = on_press
            self.on_release = on_release

        def start(self) -> None: ...

        def stop(self) -> None: ...

    class _StubGlobalHotKeys:  # minimal stand-in; real behaviour is monkeypatched per-test
        def __init__(self, mapping: dict) -> None:
            self._mapping = mapping

        def start(self) -> None: ...

        def stop(self) -> None: ...

    _keyboard.GlobalHotKeys = _StubGlobalHotKeys
    _keyboard.Key = _StubKey
    _keyboard.Listener = _StubListener
    _pynput.keyboard = _keyboard
    sys.modules["pynput"] = _pynput
    sys.modules["pynput.keyboard"] = _keyboard


class _FakeCapturer:
    def __init__(self):
        self.fail = False
        self.include_cursor = False

    def capture_fullscreen(self):
        if self.fail:
            raise RuntimeError("no permission")
        return CaptureResult(width=4, height=3, scale=1.0, pixels=bytes([255] * 4 * 4 * 3))

    def capture_region(self, region):  # pragma: no cover - unused here
        return self.capture_fullscreen()

    def capture_window(self, window_id):
        return self.capture_fullscreen()

    def list_windows(self):
        return []


class _FakeHotkeys:
    def __init__(self):
        self.bindings = {}
        self.started = 0
        self.stopped = 0
        self.cleared = 0
        self.raise_permission_error = False

    def register(self, combo, callback):
        self.bindings[combo] = callback

    def unregister(self, combo):
        self.bindings.pop(combo, None)

    def clear(self):
        self.cleared += 1
        self.bindings.clear()

    def start(self):
        if self.raise_permission_error:
            raise PermissionError("Input Monitoring required")
        self.started += 1

    def stop(self):
        self.stopped += 1


class _FakeAutostart:
    def __init__(self):
        self.last = None
        self.raise_oserror = False

    def set_enabled(self, enabled):
        if self.raise_oserror:
            raise OSError("disk full")
        self.last = enabled


@pytest.fixture(autouse=True)
def _isolate_blocklist(monkeypatch, tmp_path):
    """No test reads or writes the developer's real app blocklist. Default it
    to a nonexistent temp path — i.e. the empty blocklist — so capture tests
    keep their old behaviour; blocklist tests write to this path to opt in."""
    from shotquill import paths

    monkeypatch.setattr(paths, "blocklist_path", lambda: tmp_path / "blocklist.json")


@pytest.fixture
def fakes(monkeypatch):
    """Swap shotquill.app's macOS platform managers for in-memory fakes."""
    pytest.importorskip("PySide6")
    from shotquill import app as app_module

    capturer = _FakeCapturer()
    hotkeys = _FakeHotkeys()
    autostart = _FakeAutostart()

    def _make_capturer(include_cursor=False):
        capturer.include_cursor = include_cursor
        return capturer

    monkeypatch.setattr(app_module, "MacScreenCapturer", _make_capturer)
    monkeypatch.setattr(app_module, "MacHotkeyManager", lambda: hotkeys)
    monkeypatch.setattr(app_module, "MacAutostartManager", lambda: autostart)
    return capturer, hotkeys, autostart


@pytest.fixture
def config(tmp_path, qapp):
    """A ``Config`` backed by an isolated, temporary INI file (no real prefs touched)."""
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QSettings

    from shotquill.config import Config

    previous_format = QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))

    cfg = Config()
    cfg._settings.clear()
    try:
        yield cfg
    finally:
        cfg._settings.clear()
        QSettings.setDefaultFormat(previous_format)
