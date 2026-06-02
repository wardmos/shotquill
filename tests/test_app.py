# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Headless tests for the top-level application controller.

The platform managers (screen capture, global hotkeys, launch-at-login) are
replaced with fakes so the orchestration logic — hotkey registration, the capture
success/failure paths, autostart syncing, window bookkeeping — can be exercised
offscreen without touching real system frameworks.
"""

import pytest

pytest.importorskip("PySide6")

from shotquill import app as app_module  # noqa: E402
from shotquill.capture.base import CaptureResult  # noqa: E402


class _FakeCapturer:
    def __init__(self):
        self.fail = False

    def capture_fullscreen(self):
        if self.fail:
            raise RuntimeError("no permission")
        return CaptureResult(width=4, height=3, scale=1.0, pixels=bytes([255] * 4 * 4 * 3))

    def capture_region(self, region):  # pragma: no cover - unused here
        return self.capture_fullscreen()


class _FakeHotkeys:
    def __init__(self):
        self.bindings = {}
        self.started = 0
        self.stopped = 0
        self.cleared = 0

    def register(self, combo, callback):
        self.bindings[combo] = callback

    def unregister(self, combo):
        self.bindings.pop(combo, None)

    def clear(self):
        self.cleared += 1
        self.bindings.clear()

    def start(self):
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


@pytest.fixture
def fakes(monkeypatch):
    capturer = _FakeCapturer()
    hotkeys = _FakeHotkeys()
    autostart = _FakeAutostart()
    monkeypatch.setattr(app_module, "MacScreenCapturer", lambda: capturer)
    monkeypatch.setattr(app_module, "MacHotkeyManager", lambda: hotkeys)
    monkeypatch.setattr(app_module, "MacAutostartManager", lambda: autostart)
    return capturer, hotkeys, autostart


def _build_app(qapp, fakes):
    return app_module.ShotquillApp(qapp)


def test_build_icon_is_not_null(qapp):
    icon = app_module._build_icon()
    assert not icon.isNull()


def test_apply_hotkeys_registers_both_capture_combos(qapp, config, fakes):
    _capturer, hotkeys, _autostart = fakes
    app = _build_app(qapp, fakes)
    assert set(hotkeys.bindings) == {"<alt>+a", "<alt>+s"}
    assert hotkeys.started >= 1
    app.shutdown()


def test_grab_returns_qimage_on_success(qapp, config, fakes):
    app = _build_app(qapp, fakes)
    image = app._grab()
    assert image is not None
    assert (image.width(), image.height()) == (4, 3)
    app.shutdown()


def test_grab_returns_none_on_capture_failure(qapp, config, fakes):
    capturer, _hotkeys, _autostart = fakes
    app = _build_app(qapp, fakes)
    capturer.fail = True
    assert app._grab() is None  # error is reported via the tray, not raised
    app.shutdown()


def test_sync_autostart_follows_config(qapp, config, fakes):
    config.set_autostart(True)
    _capturer, _hotkeys, autostart = fakes
    app = _build_app(qapp, fakes)
    assert autostart.last is True
    app.shutdown()


def test_sync_autostart_swallows_oserror(qapp, config, fakes):
    _capturer, _hotkeys, autostart = fakes
    autostart.raise_oserror = True
    # Construction calls _sync_autostart; an OSError there must not crash startup.
    app = _build_app(qapp, fakes)
    app.shutdown()


def test_capture_fullscreen_opens_editor(qapp, config, fakes, monkeypatch):
    app = _build_app(qapp, fakes)
    opened = []
    monkeypatch.setattr(app, "_open_editor", opened.append)
    app._capture_fullscreen()
    assert len(opened) == 1
    assert (opened[0].width(), opened[0].height()) == (4, 3)
    app.shutdown()


def test_capture_fullscreen_does_nothing_on_failure(qapp, config, fakes, monkeypatch):
    capturer, _hotkeys, _autostart = fakes
    capturer.fail = True
    app = _build_app(qapp, fakes)
    opened = []
    monkeypatch.setattr(app, "_open_editor", opened.append)
    app._capture_fullscreen()
    assert opened == []
    app.shutdown()


def test_track_and_forget_window_bookkeeping(qapp, config, fakes):
    from PySide6.QtWidgets import QWidget

    app = _build_app(qapp, fakes)
    widget = QWidget()
    app._track(widget)
    assert widget in app._windows
    app._forget(widget)
    assert widget not in app._windows
    app.shutdown()


def test_shutdown_stops_hotkeys(qapp, config, fakes):
    _capturer, hotkeys, _autostart = fakes
    app = _build_app(qapp, fakes)
    before = hotkeys.stopped
    app.shutdown()
    assert hotkeys.stopped > before


def test_open_settings_reapplies_on_accept(qapp, config, fakes, monkeypatch):
    app = _build_app(qapp, fakes)

    class _FakeDialog:
        def __init__(self, cfg):
            self._cfg = cfg

        def exec(self):
            return True

    monkeypatch.setattr(app_module, "SettingsDialog", _FakeDialog)
    rebuilt = []
    monkeypatch.setattr(app, "_rebuild_menu", lambda: rebuilt.append(True))
    monkeypatch.setattr(app, "_apply_hotkeys", lambda: rebuilt.append("hotkeys"))
    app._open_settings()
    assert "hotkeys" in rebuilt and True in rebuilt
    app.shutdown()
