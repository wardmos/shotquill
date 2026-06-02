# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the macOS capturer's pixel normalization and region mapping.

``mss`` is replaced with a fake so the BGRA->RGBA reordering and the region
bounding-box translation are verified without touching a real display.
"""

from shotquill.capture import macos
from shotquill.capture.base import Rect


class _FakeShot:
    def __init__(self, size, bgra):
        self.size = size
        self.bgra = bgra


class _FakeSct:
    # monitors[0] is the virtual screen spanning every display.
    monitors = [{"left": 0, "top": 0, "width": 1, "height": 1}]

    def __init__(self, shot):
        self._shot = shot
        self.grabbed = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def grab(self, target):
        self.grabbed = target
        return self._shot


def _install_fake(monkeypatch, shot):
    sct = _FakeSct(shot)
    monkeypatch.setattr(macos.mss, "mss", lambda: sct)
    return sct


def test_fullscreen_grabs_virtual_screen_and_normalizes_to_rgba(monkeypatch):
    # One blue pixel in BGRA: B=255, G=0, R=0, A=255.
    shot = _FakeShot((1, 1), bytes([255, 0, 0, 255]))
    sct = _install_fake(monkeypatch, shot)

    result = macos.MacScreenCapturer().capture_fullscreen()

    assert sct.grabbed is _FakeSct.monitors[0]
    assert (result.width, result.height) == (1, 1)
    # Reordered to RGBA: R=0, G=0, B=255, A=255.
    assert result.pixels == bytes([0, 0, 255, 255])


def test_region_translates_rect_into_mss_bbox(monkeypatch):
    shot = _FakeShot((2, 1), bytes([0, 0, 0, 255, 0, 0, 0, 255]))
    sct = _install_fake(monkeypatch, shot)

    macos.MacScreenCapturer().capture_region(Rect(x=10, y=20, width=2, height=1))

    assert sct.grabbed == {"left": 10, "top": 20, "width": 2, "height": 1}


def test_capture_result_reports_native_scale(monkeypatch):
    shot = _FakeShot((1, 1), bytes([0, 0, 0, 255]))
    _install_fake(monkeypatch, shot)
    result = macos.MacScreenCapturer().capture_fullscreen()
    assert result.scale == 1.0
