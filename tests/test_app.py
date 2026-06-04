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
        self.include_cursor = False

    def capture_fullscreen(self):
        if self.fail:
            raise RuntimeError("no permission")
        return CaptureResult(width=4, height=3, scale=1.0, pixels=bytes([255] * 4 * 4 * 3))

    def capture_region(self, region):  # pragma: no cover - unused here
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


@pytest.fixture
def fakes(monkeypatch):
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


def _build_app(qapp, fakes):
    return app_module.ShotquillApp(qapp)


def test_build_icon_is_not_null(qapp):
    icon = app_module._build_icon()
    assert not icon.isNull()


def test_app_is_qobject_for_queued_hotkey_delivery(qapp, config, fakes):
    # ShotquillApp must be a QObject so the hotkey bridge's signals — emitted
    # from pynput's listener thread — reach the capture slots via a *queued*
    # connection onto the GUI thread. A plain-object receiver makes Qt fall back
    # to a direct call on the listener thread, where building the overlay/editor
    # QWidgets crashes on macOS and the hotkey appears dead (menu still works).
    from PySide6.QtCore import QObject

    app = _build_app(qapp, fakes)
    assert isinstance(app, QObject)
    app.shutdown()


def test_apply_hotkeys_registers_all_capture_combos(qapp, config, fakes):
    _capturer, hotkeys, _autostart = fakes
    app = _build_app(qapp, fakes)
    assert set(hotkeys.bindings) == {"<alt>+a", "<alt>+s"}
    assert hotkeys.started >= 1
    app.shutdown()


def test_apply_hotkeys_skips_disabled_combos(qapp, config, fakes):
    config.set_hotkey_enabled("fullscreen_capture", False)
    _capturer, hotkeys, _autostart = fakes
    app = _build_app(qapp, fakes)
    # The disabled fullscreen combo is never registered with the listener.
    assert set(hotkeys.bindings) == {"<alt>+a"}
    app.shutdown()


def test_apply_hotkeys_opens_input_monitoring_when_permission_missing(
    qapp, config, fakes, monkeypatch
):
    _capturer, hotkeys, _autostart = fakes
    hotkeys.raise_permission_error = True
    opened = []
    messages = []
    monkeypatch.setattr(app_module.subprocess, "run", lambda args, check=False: opened.append(args))
    monkeypatch.setattr(
        app_module.QSystemTrayIcon, "showMessage", lambda *args: messages.append(args)
    )

    app = _build_app(qapp, fakes)

    assert opened == [["open", app_module._PRIVACY_INPUT_MONITORING]]
    assert messages
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


def test_capture_fullscreen_opens_editor_over_the_screen(qapp, config, fakes, monkeypatch):
    # With auto-output off, a capture falls through to the editor, placed over
    # the captured area (the whole virtual desktop for a full-screen shot).
    config.set_auto_save_after_capture(False)
    config.set_auto_copy_after_capture(False)
    app = _build_app(qapp, fakes)
    opened = []
    monkeypatch.setattr(
        app, "_open_editor", lambda image, origin=None: opened.append((image, origin))
    )
    app._capture_fullscreen()
    assert len(opened) == 1
    image, origin = opened[0]
    assert (image.width(), image.height()) == (4, 3)
    assert origin == qapp.primaryScreen().virtualGeometry()
    app.shutdown()


def test_capture_fullscreen_does_nothing_on_failure(qapp, config, fakes, monkeypatch):
    capturer, _hotkeys, _autostart = fakes
    capturer.fail = True
    app = _build_app(qapp, fakes)
    opened = []
    monkeypatch.setattr(
        app, "_open_editor", lambda image, origin=None: opened.append((image, origin))
    )
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


def test_capturer_gets_cursor_preference_from_config(qapp, config, fakes):
    config.set_include_cursor(True)
    capturer, _hotkeys, _autostart = fakes
    app = _build_app(qapp, fakes)
    assert capturer.include_cursor is True
    app.shutdown()


def test_open_settings_syncs_capturer_cursor_preference(qapp, config, fakes, monkeypatch):
    capturer, _hotkeys, _autostart = fakes
    app = _build_app(qapp, fakes)
    assert capturer.include_cursor is False

    class _FakeDialog:
        def __init__(self, cfg):
            pass

        def exec(self):
            # Simulate the user turning the cursor toggle on in the dialog.
            config.set_include_cursor(True)
            return True

    monkeypatch.setattr(app_module, "SettingsDialog", _FakeDialog)
    app._open_settings()
    assert capturer.include_cursor is True
    app.shutdown()


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
