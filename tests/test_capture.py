# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the macOS capturer.

Quartz is macOS-only, so it is replaced with a fake module and the CGImage->RGBA
conversion is stubbed. The point of these tests is to lock in that every capture
path requests ``kCGWindowImageBestResolution`` — without it CoreGraphics returns
1x (point) images and Retina screenshots look soft.
"""

import sys
import types

import pytest

from shotquill.capture import macos
from shotquill.capture.base import CaptureResult, Rect

# Quartz image-option flags (values mirror CoreGraphics' CGWindowImageOption).
_BEST_RESOLUTION = 1 << 3
_IGNORE_FRAMING = 1 << 0


def _fake_quartz(record):
    def create_image(bounds, opt, wid, img):
        record.update(bounds=bounds, opt=opt, wid=wid, img=img)
        return "CGIMAGE"

    return types.SimpleNamespace(
        CGRectInfinite="INFINITE",
        CGRectNull="NULL",
        CGRectMake=lambda x, y, w, h: ("rect", x, y, w, h),
        kCGWindowListOptionOnScreenOnly=1,
        kCGWindowListOptionIncludingWindow=1 << 3,
        kCGNullWindowID=0,
        kCGWindowImageBestResolution=_BEST_RESOLUTION,
        kCGWindowImageBoundsIgnoreFraming=_IGNORE_FRAMING,
        CGWindowListCreateImage=create_image,
    )


@pytest.fixture
def quartz(monkeypatch):
    record = {}
    monkeypatch.setitem(sys.modules, "Quartz", _fake_quartz(record))
    # Bypass the real CGImage bitmap pipeline; we only assert the capture call.
    monkeypatch.setattr(
        macos.MacScreenCapturer,
        "_cgimage_to_result",
        staticmethod(
            lambda cg: CaptureResult(
                width=4, height=2, scale=1.0, pixels=b"x" * 32, premultiplied=True
            )
        ),
    )
    return record


def test_fullscreen_captures_virtual_screen_at_best_resolution(quartz):
    result = macos.MacScreenCapturer().capture_fullscreen()
    assert quartz["bounds"] == "INFINITE"  # CGRectInfinite spans all displays
    assert quartz["img"] & _BEST_RESOLUTION
    assert (result.width, result.height) == (4, 2)


def test_region_maps_rect_and_requests_best_resolution(quartz):
    macos.MacScreenCapturer().capture_region(Rect(x=10, y=20, width=30, height=40))
    assert quartz["bounds"] == ("rect", 10, 20, 30, 40)
    assert quartz["img"] & _BEST_RESOLUTION


def test_window_capture_requests_best_resolution_and_ignores_framing(quartz):
    macos.MacScreenCapturer().capture_window(1234)
    assert quartz["wid"] == 1234
    assert quartz["img"] & _BEST_RESOLUTION
    assert quartz["img"] & _IGNORE_FRAMING


def test_fullscreen_raises_when_capture_fails(monkeypatch):
    record = {}
    fake = _fake_quartz(record)
    fake.CGWindowListCreateImage = lambda *a: None
    monkeypatch.setitem(sys.modules, "Quartz", fake)
    with pytest.raises(RuntimeError):
        macos.MacScreenCapturer().capture_fullscreen()
