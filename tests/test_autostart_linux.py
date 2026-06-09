# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the Linux XDG autostart manager and platform factories."""

from __future__ import annotations

import pytest

from shotquill import autostart, hotkeys
from shotquill.autostart import linux as autostart_linux


@pytest.fixture
def autostart_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path / "config" / "autostart" / "shotquill.desktop"


def test_enable_writes_desktop_entry(autostart_home):
    mgr = autostart_linux.LinuxAutostartManager()
    assert mgr.is_enabled() is False
    mgr.enable()
    assert mgr.is_enabled() is True
    body = autostart_home.read_text(encoding="utf-8")
    assert "[Desktop Entry]" in body
    assert "X-GNOME-Autostart-enabled=true" in body
    assert "Exec=" in body


def test_disable_is_idempotent(autostart_home):
    mgr = autostart_linux.LinuxAutostartManager()
    mgr.enable()
    mgr.disable()
    assert mgr.is_enabled() is False
    mgr.disable()  # second disable must not raise


def test_set_enabled_toggles(autostart_home):
    mgr = autostart_linux.LinuxAutostartManager()
    mgr.set_enabled(True)
    assert mgr.is_enabled() is True
    mgr.set_enabled(False)
    assert mgr.is_enabled() is False


def test_launch_arguments_prefers_appimage_path_when_frozen(monkeypatch):
    monkeypatch.setattr(autostart_linux.sys, "frozen", True, raising=False)
    monkeypatch.setenv("APPIMAGE", "/opt/ShotQuill.AppImage")
    assert autostart_linux.launch_arguments() == ["/opt/ShotQuill.AppImage"]


def test_launch_arguments_dev_runs_module(monkeypatch):
    monkeypatch.setattr(autostart_linux.sys, "frozen", False, raising=False)
    args = autostart_linux.launch_arguments()
    assert args[1:] == ["-m", "shotquill"]


def test_exec_line_quotes_paths_with_spaces():
    line = autostart_linux._exec_line(["/home/a b/ShotQuill.AppImage"])
    assert line == '"/home/a b/ShotQuill.AppImage"'


def test_hotkeys_factory_routes_by_platform(monkeypatch):
    monkeypatch.setattr(hotkeys.sys, "platform", "linux")
    from shotquill.hotkeys.linux import LinuxHotkeyManager

    assert isinstance(hotkeys.get_manager(), LinuxHotkeyManager)


def test_autostart_factory_routes_by_platform(monkeypatch):
    monkeypatch.setattr(autostart.sys, "platform", "linux")
    assert isinstance(autostart.get_manager(), autostart_linux.LinuxAutostartManager)
