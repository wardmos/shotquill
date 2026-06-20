# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the platform factory seams.

The OCR factory has its own file (``test_ocr_factory.py``); this covers the
three parallel ``sys.platform``-branching factories — launch-at-login
(``autostart.get_manager``), global hotkeys (``hotkeys.get_manager``), and
headless capture/OCR (``headless.get_capturer`` / ``get_recognizer``) — across
every platform branch plus the unsupported-platform fallthrough.

Each factory imports its backend lazily inside the branch, so a backend for one
OS can be selected (and the constructor exercised) while running the tests on
another — the real platform call happens later, inside the manager.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

# Pre-import every backend now, under the real platform, so that faking
# ``sys.platform`` in a test only flips the factory's branch — it never re-runs a
# backend's (or the stdlib's) platform-sensitive module-level import. Notably,
# ``autostart.macos`` pulls in ``xml.sax.saxutils`` → ``urllib.request``, whose
# top-level ``darwin`` branch imports the macOS-only ``_scproxy``; importing it
# here (while the platform is still the host's) caches it before any fake bites.
import shotquill.autostart.linux  # noqa: E402,F401
import shotquill.autostart.macos  # noqa: E402,F401
import shotquill.autostart.windows  # noqa: E402,F401
import shotquill.capture.macos  # noqa: E402,F401
import shotquill.capture.qtgrab  # noqa: E402,F401
import shotquill.capture.wayland  # noqa: E402,F401
import shotquill.capture.windows  # noqa: E402,F401
import shotquill.hotkeys.linux  # noqa: E402,F401
import shotquill.hotkeys.macos  # noqa: E402,F401
import shotquill.hotkeys.wayland  # noqa: E402,F401
import shotquill.hotkeys.windows  # noqa: E402,F401
import shotquill.ocr.linux  # noqa: E402,F401
import shotquill.ocr.macos  # noqa: E402,F401
import shotquill.ocr.windows  # noqa: E402,F401
from shotquill import autostart, headless, hotkeys

# --- autostart.get_manager ------------------------------------------------


def test_autostart_macos(monkeypatch):
    monkeypatch.setattr(autostart.sys, "platform", "darwin")
    from shotquill.autostart.macos import MacAutostartManager

    assert isinstance(autostart.get_manager(), MacAutostartManager)


def test_autostart_linux(monkeypatch):
    monkeypatch.setattr(autostart.sys, "platform", "linux")
    from shotquill.autostart.linux import LinuxAutostartManager

    assert isinstance(autostart.get_manager(), LinuxAutostartManager)


def test_autostart_windows(monkeypatch):
    monkeypatch.setattr(autostart.sys, "platform", "win32")
    from shotquill.autostart.windows import WindowsAutostartManager

    assert isinstance(autostart.get_manager(), WindowsAutostartManager)


def test_autostart_unknown_platform_raises(monkeypatch):
    monkeypatch.setattr(autostart.sys, "platform", "sunos5")
    with pytest.raises(RuntimeError, match="no autostart backend"):
        autostart.get_manager()


# --- hotkeys.get_manager --------------------------------------------------


def test_hotkeys_macos(monkeypatch):
    monkeypatch.setattr(hotkeys.sys, "platform", "darwin")
    from shotquill.hotkeys.macos import MacHotkeyManager

    assert isinstance(hotkeys.get_manager(), MacHotkeyManager)


def test_hotkeys_linux_x11(monkeypatch):
    monkeypatch.setattr(hotkeys.sys, "platform", "linux")
    monkeypatch.setattr(hotkeys, "_is_wayland_session", lambda: False)
    from shotquill.hotkeys.linux import LinuxHotkeyManager

    assert isinstance(hotkeys.get_manager(), LinuxHotkeyManager)


def test_hotkeys_linux_wayland(monkeypatch):
    monkeypatch.setattr(hotkeys.sys, "platform", "linux")
    monkeypatch.setattr(hotkeys, "_is_wayland_session", lambda: True)
    from shotquill.hotkeys.wayland import WaylandHotkeyManager

    assert isinstance(hotkeys.get_manager(), WaylandHotkeyManager)


def test_hotkeys_windows(monkeypatch):
    monkeypatch.setattr(hotkeys.sys, "platform", "win32")
    from shotquill.hotkeys.windows import WindowsHotkeyManager

    assert isinstance(hotkeys.get_manager(), WindowsHotkeyManager)


def test_hotkeys_unknown_platform_raises(monkeypatch):
    monkeypatch.setattr(hotkeys.sys, "platform", "sunos5")
    with pytest.raises(RuntimeError, match="no global-hotkey backend"):
        hotkeys.get_manager()


def test_hotkeys_is_wayland_session(monkeypatch):
    # An explicit QT_QPA_PLATFORM (e.g. offscreen in tests, or a forced xcb) wins
    # over any Wayland hint, so the X11/pynput path stays selectable.
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert hotkeys._is_wayland_session() is False

    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    assert hotkeys._is_wayland_session() is True

    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert hotkeys._is_wayland_session() is False


# --- headless.get_capturer ------------------------------------------------


def test_get_capturer_macos(monkeypatch):
    monkeypatch.setattr(headless.sys, "platform", "darwin")
    from shotquill.capture.macos import MacScreenCapturer

    assert isinstance(headless.get_capturer(), MacScreenCapturer)


def test_get_capturer_linux_x11(monkeypatch):
    monkeypatch.setattr(headless.sys, "platform", "linux")
    monkeypatch.setattr(headless, "_is_wayland_session", lambda: False)
    from shotquill.capture.qtgrab import QtGrabCapturer

    assert isinstance(headless.get_capturer(), QtGrabCapturer)


def test_get_capturer_linux_wayland(monkeypatch):
    monkeypatch.setattr(headless.sys, "platform", "linux")
    monkeypatch.setattr(headless, "_is_wayland_session", lambda: True)
    from shotquill.capture.wayland import PortalScreenCapturer

    assert isinstance(headless.get_capturer(), PortalScreenCapturer)


def test_get_capturer_windows(monkeypatch):
    monkeypatch.setattr(headless.sys, "platform", "win32")
    from shotquill.capture.windows import WindowsScreenCapturer

    assert isinstance(headless.get_capturer(), WindowsScreenCapturer)


def test_get_capturer_unknown_platform_raises(monkeypatch):
    monkeypatch.setattr(headless.sys, "platform", "sunos5")
    with pytest.raises(headless.CapabilityUnsupported):
        headless.get_capturer()


# --- headless.get_recognizer (distinct from ocr.get_recognizer: it raises
# CapabilityUnsupported instead of returning None) --------------------------


def test_get_recognizer_macos(monkeypatch):
    monkeypatch.setattr(headless.sys, "platform", "darwin")
    from shotquill.ocr.macos import VisionTextRecognizer

    assert isinstance(headless.get_recognizer(), VisionTextRecognizer)


def test_get_recognizer_linux_with_tesseract(monkeypatch):
    monkeypatch.setattr(headless.sys, "platform", "linux")
    from shotquill.ocr import linux

    monkeypatch.setattr(linux, "tesseract_path", lambda: "/usr/bin/tesseract")
    assert isinstance(headless.get_recognizer(), linux.TesseractTextRecognizer)


def test_get_recognizer_linux_without_tesseract_raises(monkeypatch):
    monkeypatch.setattr(headless.sys, "platform", "linux")
    from shotquill.ocr import linux

    monkeypatch.setattr(linux, "tesseract_path", lambda: None)
    with pytest.raises(headless.CapabilityUnsupported, match="Tesseract"):
        headless.get_recognizer()


def test_get_recognizer_windows_available(monkeypatch):
    monkeypatch.setattr(headless.sys, "platform", "win32")
    from shotquill.ocr import windows

    sentinel = object()
    monkeypatch.setattr(windows, "is_available", lambda: True)
    monkeypatch.setattr(windows, "WindowsOcrRecognizer", lambda: sentinel)
    assert headless.get_recognizer() is sentinel


def test_get_recognizer_windows_unavailable_raises(monkeypatch):
    monkeypatch.setattr(headless.sys, "platform", "win32")
    from shotquill.ocr import windows

    monkeypatch.setattr(windows, "is_available", lambda: False)
    with pytest.raises(headless.CapabilityUnsupported, match="WinRT"):
        headless.get_recognizer()


def test_get_recognizer_unknown_platform_raises(monkeypatch):
    monkeypatch.setattr(headless.sys, "platform", "sunos5")
    with pytest.raises(headless.CapabilityUnsupported):
        headless.get_recognizer()
