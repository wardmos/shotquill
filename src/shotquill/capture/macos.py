# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""macOS screen capture.

All capture (full-screen, region, single window) goes through Quartz'
``CGWindowListCreateImage``. The crucial flag is ``kCGWindowImageBestResolution``:
without it CoreGraphics returns images at *point* (1x) resolution, so on a Retina
display screenshots come out at roughly half the native pixel count and look
soft. With it we get true native-resolution pixels. PyObjC/Quartz is macOS-only
and so is imported lazily — this module still imports cleanly on Linux (for CI).

Pixels are normalized to RGBA so downstream code (PIL saver, Qt clipboard) does
not need to know about the capture backend.
"""

from __future__ import annotations

import os

from shotquill.capture.base import CaptureResult, Rect, ScreenCapturer, WindowInfo


class MacScreenCapturer(ScreenCapturer):
    def capture_fullscreen(self) -> CaptureResult:
        import Quartz

        # CGRectInfinite spans every display in the virtual desktop.
        return self._grab_rect(Quartz.CGRectInfinite)

    def capture_region(self, region: Rect) -> CaptureResult:
        import Quartz

        rect = Quartz.CGRectMake(region.x, region.y, region.width, region.height)
        return self._grab_rect(rect)

    @staticmethod
    def _grab_rect(rect) -> CaptureResult:
        """Capture a CoreGraphics rect at native (Retina) resolution."""
        import Quartz

        cg_image = Quartz.CGWindowListCreateImage(
            rect,
            Quartz.kCGWindowListOptionOnScreenOnly,
            Quartz.kCGNullWindowID,
            # Best resolution = native Retina pixels (default would be 1x).
            Quartz.kCGWindowImageBestResolution,
        )
        if cg_image is None:
            raise RuntimeError("screen capture failed")
        return MacScreenCapturer._cgimage_to_result(cg_image)

    def list_windows(self) -> list[WindowInfo]:
        import Quartz

        options = (
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
        )
        raw = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []
        own_pid = os.getpid()
        windows: list[WindowInfo] = []
        for info in raw:
            # Layer 0 == normal application windows; skip the menu bar, Dock,
            # wallpaper, and other chrome that lives on other layers.
            if int(info.get("kCGWindowLayer", 0)) != 0:
                continue
            # Don't offer our own overlay / editor windows as capture targets.
            if int(info.get("kCGWindowOwnerPID", 0)) == own_pid:
                continue
            bounds = info.get("kCGWindowBounds")
            if not bounds:
                continue
            x, y = int(bounds["X"]), int(bounds["Y"])
            w, h = int(bounds["Width"]), int(bounds["Height"])
            if w < 1 or h < 1:
                continue
            windows.append(
                WindowInfo(
                    window_id=int(info.get("kCGWindowNumber", 0)),
                    owner=str(info.get("kCGWindowOwnerName", "") or ""),
                    # kCGWindowName is only populated with Screen Recording
                    # permission; fall back to an empty title otherwise.
                    title=str(info.get("kCGWindowName", "") or ""),
                    bounds=Rect(x, y, w, h),
                )
            )
        # CGWindowListCopyWindowInfo returns windows front-to-back already.
        return windows

    def capture_window(self, window_id: int) -> CaptureResult:
        import Quartz

        cg_image = Quartz.CGWindowListCreateImage(
            Quartz.CGRectNull,
            Quartz.kCGWindowListOptionIncludingWindow,
            window_id,
            # Native Retina pixels, and don't pad with the window's drop shadow.
            Quartz.kCGWindowImageBoundsIgnoreFraming | Quartz.kCGWindowImageBestResolution,
        )
        if cg_image is None:
            raise RuntimeError(f"window {window_id} could not be captured")
        return self._cgimage_to_result(cg_image)

    @staticmethod
    def _cgimage_to_result(cg_image) -> CaptureResult:
        import Quartz

        width = int(Quartz.CGImageGetWidth(cg_image))
        height = int(Quartz.CGImageGetHeight(cg_image))
        bytes_per_row = width * 4
        # Draw the window image into a freshly allocated, padding-free RGBA
        # buffer. The bitmap context only supports premultiplied alpha, so we
        # flag the result as premultiplied for the imaging layer.
        buffer = bytearray(bytes_per_row * height)
        color_space = Quartz.CGColorSpaceCreateDeviceRGB()
        context = Quartz.CGBitmapContextCreate(
            buffer,
            width,
            height,
            8,
            bytes_per_row,
            color_space,
            Quartz.kCGImageAlphaPremultipliedLast | Quartz.kCGBitmapByteOrder32Big,
        )
        Quartz.CGContextDrawImage(context, Quartz.CGRectMake(0, 0, width, height), cg_image)
        return CaptureResult(
            width=width,
            height=height,
            scale=1.0,
            pixels=bytes(buffer),
            premultiplied=True,
        )
