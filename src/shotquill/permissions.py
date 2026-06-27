# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""macOS privacy permissions: status checks and System Settings deep links.

ShotQuill needs the Screen Recording TCC permission for captures. This module
only *reads* permission state (via the Quartz preflight helpers, macOS 10.15+)
and jumps to the right System Settings pane. Tests and Linux development import
it without PyObjC, so every check degrades to UNKNOWN instead of failing.
"""

from __future__ import annotations

import subprocess
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

SCREEN_CAPTURE_PANE = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
)
INPUT_MONITORING_PANE = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
)


class PermissionStatus(Enum):
    GRANTED = "granted"
    DENIED = "denied"
    UNKNOWN = "unknown"  # not determinable: non-mac, no PyObjC, or an old macOS


def quartz_function(name: str) -> Callable[[], bool] | None:
    """Return a Quartz TCC helper when running on macOS, else ``None``.

    Tests and Linux development import this module without PyObjC/Quartz, so
    permission preflights must be best-effort; callers decide what a missing
    helper means (the settings UI shows UNKNOWN, the hotkey gate fails open).
    """
    try:
        import Quartz  # type: ignore[import-not-found]
    except Exception:
        return None
    return getattr(Quartz, name, None)


def _preflight_status(name: str) -> PermissionStatus:
    preflight = quartz_function(name)
    if preflight is None:
        return PermissionStatus.UNKNOWN
    try:
        granted = bool(preflight())
    except Exception:
        return PermissionStatus.UNKNOWN
    return PermissionStatus.GRANTED if granted else PermissionStatus.DENIED


def screen_capture_status() -> PermissionStatus:
    """Whether macOS currently lets this process record the screen."""
    return _preflight_status("CGPreflightScreenCaptureAccess")


def input_monitoring_status() -> PermissionStatus:
    """Whether macOS currently lets this process listen for key events."""
    return _preflight_status("CGPreflightListenEventAccess")


def open_screen_capture_pane() -> None:
    subprocess.run(["open", SCREEN_CAPTURE_PANE], check=False)


def open_input_monitoring_pane() -> None:
    subprocess.run(["open", INPUT_MONITORING_PANE], check=False)
