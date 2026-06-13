# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Windows platform seams: config / log directories and the capture factory."""

from __future__ import annotations

from pathlib import Path

import pytest

from shotquill import headless, paths


def test_config_dir_uses_appdata(tmp_path, monkeypatch):
    # Per-user app config conventionally lives under roaming %APPDATA%.
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert paths.config_dir() == tmp_path / "Roaming" / "shotquill"


def test_config_dir_falls_back_to_appdata_roaming_when_env_unset(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert paths.config_dir() == tmp_path / "AppData" / "Roaming" / "shotquill"


def test_audit_log_path_uses_localappdata(tmp_path, monkeypatch):
    # Logs are machine-local state, not roaming config: %LOCALAPPDATA%.
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    log = paths.audit_log_path()
    assert log == tmp_path / "Local" / "shotquill" / "Logs" / "audit.log"
    # The parent is created so callers can append without ceremony.
    assert log.parent.is_dir()


def test_audit_log_path_ignores_xdg_on_windows(tmp_path, monkeypatch):
    # XDG vars may leak in (e.g. from an MSYS/Cygwin shell); they must not steer
    # the Windows path away from %LOCALAPPDATA%.
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    log = paths.audit_log_path()
    assert log == tmp_path / "Local" / "shotquill" / "Logs" / "audit.log"
    assert not (tmp_path / "xdg").exists()


def test_get_capturer_routes_to_qtgrab_on_windows(qapp, monkeypatch):
    # Windows has no out-of-band grab restriction, so QScreen.grabWindow covers
    # full-screen / region capture with no extra dependency. ``qapp`` already
    # owns a QGuiApplication, so no platform-specific session setup runs.
    pytest.importorskip("PySide6")
    from shotquill.capture.qtgrab import QtGrabCapturer

    monkeypatch.setattr(headless.sys, "platform", "win32")
    capturer = headless.get_capturer()
    assert isinstance(capturer, QtGrabCapturer)


def test_get_recognizer_unsupported_on_windows(monkeypatch):
    # On-device OCR is macOS-only today; Windows reports it as unavailable
    # (the same typed signal as Linux) rather than offering a button that fails.
    monkeypatch.setattr(headless.sys, "platform", "win32")
    with pytest.raises(headless.CapabilityUnsupported):
        headless.get_recognizer()
