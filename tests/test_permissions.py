# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the macOS permission status checks and System Settings links.

Headless/Linux CI has no Quartz, so the real preflights are exercised through
monkeypatched helpers; what's actually under test is the mapping to
PermissionStatus and the fail-to-UNKNOWN behavior.
"""

from shotquill import permissions
from shotquill.permissions import PermissionStatus


def test_status_is_unknown_without_quartz(monkeypatch):
    # No PyObjC/Quartz (Linux dev, tests): the state can't be read.
    monkeypatch.setattr(permissions, "quartz_function", lambda name: None)
    assert permissions.screen_capture_status() is PermissionStatus.UNKNOWN
    assert permissions.input_monitoring_status() is PermissionStatus.UNKNOWN


def test_preflight_results_map_to_granted_and_denied(monkeypatch):
    monkeypatch.setattr(permissions, "quartz_function", lambda name: lambda: True)
    assert permissions.screen_capture_status() is PermissionStatus.GRANTED

    monkeypatch.setattr(permissions, "quartz_function", lambda name: lambda: False)
    assert permissions.input_monitoring_status() is PermissionStatus.DENIED


def test_preflight_failure_reads_as_unknown(monkeypatch):
    def boom():
        raise OSError("window server unavailable")

    monkeypatch.setattr(permissions, "quartz_function", lambda name: boom)
    assert permissions.screen_capture_status() is PermissionStatus.UNKNOWN


def test_statuses_query_their_own_preflight(monkeypatch):
    queried = []
    monkeypatch.setattr(
        permissions, "quartz_function", lambda name: queried.append(name) or (lambda: True)
    )
    permissions.screen_capture_status()
    permissions.input_monitoring_status()
    assert queried == ["CGPreflightScreenCaptureAccess", "CGPreflightListenEventAccess"]


def test_open_pane_helpers_deep_link_the_right_panes(monkeypatch):
    opened = []
    monkeypatch.setattr(permissions.subprocess, "run", lambda cmd, check: opened.append(tuple(cmd)))
    permissions.open_screen_capture_pane()
    permissions.open_input_monitoring_pane()
    assert opened == [
        ("open", permissions.SCREEN_CAPTURE_PANE),
        ("open", permissions.INPUT_MONITORING_PANE),
    ]
    assert "Privacy_ScreenCapture" in permissions.SCREEN_CAPTURE_PANE
    assert "Privacy_ListenEvent" in permissions.INPUT_MONITORING_PANE
