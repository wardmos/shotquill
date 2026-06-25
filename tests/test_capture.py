# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the macOS capturer.

Quartz and ScreenCaptureKit are macOS-only, so both are replaced with fake
modules and the CGImage->RGBA conversion is stubbed. Two behaviours are locked
in:

1. The preferred ScreenCaptureKit path drives ``SCScreenshotManager`` with
   ``showsCursor`` off unless the user opted in — the legacy API has no cursor
   switch and bakes the pointer into shots on recent macOS.
2. The legacy ``CGWindowListCreateImage`` fallback always requests
   ``kCGWindowImageBestResolution`` — without it CoreGraphics returns 1x
   (point) images and Retina screenshots look soft.
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
        # Bitmap plumbing used by the multi-display composite.
        kCGImageAlphaPremultipliedLast=1,
        kCGBitmapByteOrder32Big=4 << 12,
        CGColorSpaceCreateDeviceRGB=lambda: "RGB",
        CGBitmapContextCreate=lambda *a: "CTX",
        CGContextDrawImage=lambda ctx, rect, img: record.setdefault("draws", []).append(
            (rect, img)
        ),
    )


@pytest.fixture
def quartz(monkeypatch):
    record = {}
    monkeypatch.setitem(sys.modules, "Quartz", _fake_quartz(record))
    # Block any real ScreenCaptureKit (macOS CI) — tests opt back in with the
    # fake via _install_sck. A None entry makes `import ScreenCaptureKit` fail.
    monkeypatch.setitem(sys.modules, "ScreenCaptureKit", None)
    # Bypass the real CGImage bitmap pipeline; we only assert the capture call.
    monkeypatch.setattr(
        macos.MacScreenCapturer,
        "_cgimage_to_result",
        staticmethod(
            lambda cg, scale=1.0: CaptureResult(
                width=4, height=2, scale=scale, pixels=b"x" * 32, premultiplied=True
            )
        ),
    )
    return record


# --- fake ScreenCaptureKit ---------------------------------------------------


def _ns_rect(x, y, w, h):
    return types.SimpleNamespace(
        origin=types.SimpleNamespace(x=x, y=y),
        size=types.SimpleNamespace(width=w, height=h),
    )


class _FakeDisplay:
    def __init__(self, x, y, w, h, scale=2.0):
        self._frame = _ns_rect(x, y, w, h)
        self.scale = scale

    def frame(self):
        return self._frame


class _FakeWindow:
    def __init__(self, window_id, frame):
        self._id = window_id
        self._frame = frame

    def windowID(self):
        return self._id

    def frame(self):
        return self._frame


def _fake_sck(record, displays, windows=()):
    """A stand-in ScreenCaptureKit module recording the screenshot config."""

    class _Config:
        def setWidth_(self, v):
            record["sck_width"] = v

        def setHeight_(self, v):
            record["sck_height"] = v

        def setShowsCursor_(self, v):
            record["shows_cursor"] = v

        def setIgnoreShadowsSingleWindow_(self, v):
            record["ignore_shadows"] = v

    class _Filter:
        def __init__(self, rect, scale):
            self._rect, self._scale = rect, scale

        def contentRect(self):
            return self._rect

        def pointPixelScale(self):
            return self._scale

    class _FilterAlloc:
        @staticmethod
        def initWithDisplay_excludingWindows_(display, excluded):
            frame = display.frame()
            return _Filter(_ns_rect(0, 0, frame.size.width, frame.size.height), display.scale)

        @staticmethod
        def initWithDesktopIndependentWindow_(window):
            record["filtered_window"] = window
            return _Filter(window.frame(), 2.0)

    class _Content:
        @staticmethod
        def displays():
            return list(displays)

        @staticmethod
        def windows():
            return list(windows)

    sck = types.ModuleType("ScreenCaptureKit")
    sck.SCStreamConfiguration = types.SimpleNamespace(
        alloc=lambda: types.SimpleNamespace(init=_Config)
    )
    sck.SCContentFilter = types.SimpleNamespace(alloc=lambda: _FilterAlloc)
    sck.SCShareableContent = types.SimpleNamespace(
        getShareableContentWithCompletionHandler_=lambda cb: cb(_Content, None)
    )
    sck.SCScreenshotManager = types.SimpleNamespace(
        captureImageWithFilter_configuration_completionHandler_=lambda f, cfg, cb: (
            record.setdefault("sck_shots", []).append(f),
            cb("SCK_IMAGE", None),
        )
    )
    return sck


def _install_sck(monkeypatch, record, displays, windows=()):
    monkeypatch.setitem(sys.modules, "ScreenCaptureKit", _fake_sck(record, displays, windows))


# --- ScreenCaptureKit path ---------------------------------------------------


def test_sck_excludes_cursor_by_default(quartz, monkeypatch):
    _install_sck(monkeypatch, quartz, [_FakeDisplay(0, 0, 100, 50)])
    result = macos.MacScreenCapturer().capture_fullscreen()
    assert quartz["shows_cursor"] is False
    assert "bounds" not in quartz  # legacy CGWindowListCreateImage never ran
    assert (result.width, result.height) == (4, 2)


def test_sck_includes_cursor_when_opted_in(quartz, monkeypatch):
    _install_sck(monkeypatch, quartz, [_FakeDisplay(0, 0, 100, 50)])
    macos.MacScreenCapturer(include_cursor=True).capture_fullscreen()
    assert quartz["shows_cursor"] is True


def test_fast_image_path_draws_straight_into_a_qimage(quartz, monkeypatch, qapp):
    # The overlay path must build the QImage directly from the captures, never
    # routing through CaptureResult's bytes round-trip (the copies that thrash
    # swap). _cgimage_to_result (the bytes packer) must not be touched.
    from PySide6.QtGui import QImage

    _install_sck(
        monkeypatch,
        quartz,
        [_FakeDisplay(0, 0, 100, 50, scale=2.0), _FakeDisplay(100, 10, 80, 40, scale=1.0)],
    )
    monkeypatch.setattr(
        macos.MacScreenCapturer,
        "capture_fullscreen",
        lambda *a, **k: pytest.fail("bytes round-trip used, not the fast image path"),
    )
    image = macos.MacScreenCapturer().capture_fullscreen_image()
    assert isinstance(image, QImage)
    # Same virtual-desktop geometry as the CaptureResult composite: union of the
    # frames (180x50 points) at the sharpest 2x scale.
    assert (image.width(), image.height()) == (360, 100)
    assert image.format() == QImage.Format.Format_RGBA8888_Premultiplied
    # Each display was drawn once, at its virtual-desktop position (y flipped for
    # CG's bottom-left-origin context) — no intermediate buffer.
    assert [rect for rect, _ in quartz["draws"]] == [
        ("rect", 0, 0, 200, 100),
        ("rect", 200, 0, 160, 80),
    ]


def test_fast_image_path_falls_back_to_round_trip_when_sck_unavailable(quartz, monkeypatch, qapp):
    # No ScreenCaptureKit (older macOS): the fast path returns None internally
    # and the base CaptureResult→QImage route still yields a usable QImage.
    from PySide6.QtGui import QImage

    image = macos.MacScreenCapturer().capture_fullscreen_image()
    assert isinstance(image, QImage)
    assert (image.width(), image.height()) == (4, 2)  # the stubbed CaptureResult size
    assert quartz["bounds"] == "INFINITE"  # the legacy grab ran


def test_sck_requests_native_pixel_size(quartz, monkeypatch):
    _install_sck(monkeypatch, quartz, [_FakeDisplay(0, 0, 100, 50, scale=2.0)])
    result = macos.MacScreenCapturer().capture_fullscreen()
    assert (quartz["sck_width"], quartz["sck_height"]) == (200, 100)
    # The Retina scale is reported (not hard-coded 1.0), so blocklist redaction
    # maps logical window bounds onto the right physical pixels.
    assert result.scale == 2.0


def test_sck_multi_display_composites_into_virtual_desktop(quartz, monkeypatch):
    _install_sck(
        monkeypatch,
        quartz,
        [_FakeDisplay(0, 0, 100, 50, scale=2.0), _FakeDisplay(100, 10, 80, 40, scale=1.0)],
    )
    result = macos.MacScreenCapturer().capture_fullscreen()
    # Union of the frames is 180x50 points, rendered at the sharpest (2x) scale.
    assert (result.width, result.height) == (360, 100)
    assert result.scale == 2.0  # reported so redaction maps bounds to pixels
    # Each display lands at its virtual-desktop position, y flipped for CG's
    # bottom-left-origin context.
    assert [rect for rect, _ in quartz["draws"]] == [
        ("rect", 0, 0, 200, 100),
        ("rect", 200, 0, 160, 80),
    ]


def test_sck_window_capture_targets_window_and_skips_cursor(quartz, monkeypatch):
    window = _FakeWindow(1234, _ns_rect(10, 20, 300, 200))
    _install_sck(monkeypatch, quartz, [_FakeDisplay(0, 0, 100, 50)], [window])
    macos.MacScreenCapturer().capture_window(1234)
    assert quartz["filtered_window"] is window
    assert quartz["shows_cursor"] is False
    assert quartz["ignore_shadows"] is True  # no drop-shadow padding, like legacy
    assert "wid" not in quartz  # legacy path never ran


def test_sck_window_falls_back_to_legacy_when_window_unknown(quartz, monkeypatch):
    _install_sck(monkeypatch, quartz, [_FakeDisplay(0, 0, 100, 50)], [])
    macos.MacScreenCapturer().capture_window(999)
    assert quartz["wid"] == 999  # legacy path took over


# --- legacy CGWindowListCreateImage fallback (ScreenCaptureKit blocked) -------


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
    monkeypatch.setitem(sys.modules, "ScreenCaptureKit", None)
    with pytest.raises(RuntimeError):
        macos.MacScreenCapturer().capture_fullscreen()
