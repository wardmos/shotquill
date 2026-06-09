# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""macOS screen capture.

Captures prefer ScreenCaptureKit's ``SCScreenshotManager`` (macOS 14+): it is
the only capture API with an explicit cursor switch (``showsCursor``), and the
legacy ``CGWindowListCreateImage`` — deprecated in Sonoma and rerouted through a
ScreenCaptureKit shim on recent systems — bakes the mouse pointer into every
shot with no way to opt out. Whether the pointer is included is the capturer's
``include_cursor`` flag (off by default, set from user config).

When ScreenCaptureKit is unavailable (older macOS) or fails, captures fall back
to ``CGWindowListCreateImage``. There the crucial flag is
``kCGWindowImageBestResolution``: without it CoreGraphics returns images at
*point* (1x) resolution, so on a Retina display screenshots come out at roughly
half the native pixel count and look soft. PyObjC frameworks are macOS-only and
so are imported lazily — this module still imports cleanly on Linux (for CI).

Pixels are normalized to RGBA so downstream code (QImage saver, Qt clipboard) does
not need to know about the capture backend.
"""

from __future__ import annotations

import os
from dataclasses import replace

from shotquill.capture.base import CaptureResult, Rect, ScreenCapturer, WindowInfo

# ScreenCaptureKit calls are completion-handler based; we block the calling
# thread until the handler fires. The timeout guards against a wedged capture
# service — on expiry the capture falls back to the legacy path.
_SCK_TIMEOUT = 5.0


class MacScreenCapturer(ScreenCapturer):
    def __init__(self, include_cursor: bool = False) -> None:
        self.include_cursor = include_cursor

    def capture_fullscreen(self) -> CaptureResult:
        import Quartz

        # Logical bounds of the whole virtual desktop: the top-left is the origin
        # (not (0, 0) when a monitor sits left of / above the primary), and the
        # logical width lets the legacy path recover its physical pixel scale.
        ox, oy, logical_w, _ = self._virtual_desktop_bounds()
        result = self._sck_capture_fullscreen()
        if result is None:
            # CGRectInfinite spans every display; the image is physical (Retina)
            # pixels but carries no scale of its own, so derive it from the
            # physical-to-logical width ratio.
            result = self._grab_rect(Quartz.CGRectInfinite)
            scale = result.width / logical_w if logical_w else 1.0
            result = replace(result, scale=scale)
        # Tag the origin so blocklist redaction maps window bounds (logical) onto
        # the right pixels; the SCK path already reports the correct scale.
        return replace(result, origin_x=ox, origin_y=oy)

    def capture_region(self, region: Rect) -> CaptureResult:
        # Unused at runtime (the app crops regions out of the full-screen grab),
        # so this keeps the simple legacy path.
        import Quartz

        rect = Quartz.CGRectMake(region.x, region.y, region.width, region.height)
        result = self._grab_rect(rect)
        # Physical pixels of a region requested in logical points; scale is their
        # ratio so redaction lands on the right pixels.
        scale = result.width / region.width if region.width else 1.0
        return replace(result, scale=scale, origin_x=region.x, origin_y=region.y)

    @staticmethod
    def _virtual_desktop_bounds() -> tuple[int, int, int, int]:  # pragma: no cover - macOS only
        """Logical bounds ``(x, y, width, height)`` of the whole virtual desktop
        — the union of every active display's bounds, in the same coordinate
        space as window bounds. Best-effort for redaction: any failure reading
        the display list falls back to zeros rather than breaking the capture."""
        try:
            import Quartz

            err, ids, count = Quartz.CGGetActiveDisplayList(16, None, None)
            if err or not count:
                return (0, 0, 0, 0)
            xs, ys, rights, bottoms = [], [], [], []
            for did in ids[:count]:
                b = Quartz.CGDisplayBounds(did)
                xs.append(int(b.origin.x))
                ys.append(int(b.origin.y))
                rights.append(int(b.origin.x + b.size.width))
                bottoms.append(int(b.origin.y + b.size.height))
            x, y = min(xs), min(ys)
            return (x, y, max(rights) - x, max(bottoms) - y)
        except Exception:
            return (0, 0, 0, 0)

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
        # Several windows usually share one app (and thus one pid); resolve each
        # pid's bundle id at most once.
        bundle_by_pid: dict[int, str | None] = {}
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
            pid = int(info.get("kCGWindowOwnerPID", 0))
            if pid not in bundle_by_pid:
                bundle_by_pid[pid] = self._bundle_id_for_pid(pid)
            windows.append(
                WindowInfo(
                    window_id=int(info.get("kCGWindowNumber", 0)),
                    owner=str(info.get("kCGWindowOwnerName", "") or ""),
                    # kCGWindowName is only populated with Screen Recording
                    # permission; fall back to an empty title otherwise.
                    title=str(info.get("kCGWindowName", "") or ""),
                    bounds=Rect(x, y, w, h),
                    bundle_id=bundle_by_pid[pid],
                )
            )
        # CGWindowListCopyWindowInfo returns windows front-to-back already.
        return windows

    @staticmethod
    def _bundle_id_for_pid(pid: int) -> str | None:  # pragma: no cover - macOS only
        """Resolve a running app's bundle identifier from its pid.

        The window list only carries the owner's display name and pid; the
        stable bundle id (what the blocklist matches on) comes from
        ``NSRunningApplication``. Returns ``None`` for pids with no running
        app record or no bundle id (some helper processes have none).
        """
        try:
            from AppKit import NSRunningApplication

            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        except Exception:
            return None
        if app is None:
            return None
        bundle = app.bundleIdentifier()
        return str(bundle) if bundle else None

    def capture_window(self, window_id: int) -> CaptureResult:
        import Quartz

        result = self._sck_capture_window(window_id)
        if result is not None:
            return result
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

    # --- ScreenCaptureKit path (macOS 14+) ---------------------------------

    @staticmethod
    def _sck():
        """The ScreenCaptureKit module, or None when unusable (macOS < 14)."""
        try:
            import ScreenCaptureKit
        except Exception:
            return None
        # SCScreenshotManager (one-shot screenshots) appeared in macOS 14.
        return ScreenCaptureKit if hasattr(ScreenCaptureKit, "SCScreenshotManager") else None

    @staticmethod
    def _sck_await(start):
        """Drive a completion-handler SCK call synchronously, returning its value.

        SCK delivers completions on a background queue, so blocking here (even
        on the GUI thread) does not deadlock; the timeout still guards against
        the capture service going unresponsive.
        """
        import threading

        done = threading.Event()
        out: list = [None, None]

        def completion(value, error):
            out[0], out[1] = value, error
            done.set()

        start(completion)
        if not done.wait(_SCK_TIMEOUT):
            raise RuntimeError("ScreenCaptureKit timed out")
        if out[1] is not None or out[0] is None:
            raise RuntimeError(str(out[1] or "ScreenCaptureKit returned nothing"))
        return out[0]

    def _sck_shareable_content(self, sck):
        return self._sck_await(
            lambda cb: sck.SCShareableContent.getShareableContentWithCompletionHandler_(cb)
        )

    def _sck_screenshot(self, sck, content_filter):
        """One ``SCScreenshotManager`` shot of ``content_filter`` as a CGImage."""
        rect = content_filter.contentRect()  # points
        scale = float(content_filter.pointPixelScale())
        config = sck.SCStreamConfiguration.alloc().init()
        # Explicit pixel size = native Retina resolution (default is 1x-ish).
        config.setWidth_(max(1, round(rect.size.width * scale)))
        config.setHeight_(max(1, round(rect.size.height * scale)))
        # The whole reason this path exists: the pointer is composited only
        # when the user opted in (the legacy API offers no such switch).
        config.setShowsCursor_(bool(self.include_cursor))
        # Match the legacy kCGWindowImageBoundsIgnoreFraming behaviour.
        config.setIgnoreShadowsSingleWindow_(True)
        manager = sck.SCScreenshotManager
        return self._sck_await(
            lambda cb: manager.captureImageWithFilter_configuration_completionHandler_(
                content_filter, config, cb
            )
        )

    def _sck_capture_fullscreen(self) -> CaptureResult | None:
        """All displays via ScreenCaptureKit, or None to use the legacy path."""
        sck = self._sck()
        if sck is None:
            return None
        try:
            content = self._sck_shareable_content(sck)
            displays = list(content.displays())
            if not displays:
                return None
            shots = []
            for display in displays:
                content_filter = sck.SCContentFilter.alloc().initWithDisplay_excludingWindows_(
                    display, []
                )
                scale = float(content_filter.pointPixelScale())
                shots.append((display.frame(), scale, self._sck_screenshot(sck, content_filter)))
            if len(shots) == 1:
                return self._cgimage_to_result(shots[0][2], scale=shots[0][1])
            return self._composite_displays(shots)
        except Exception:
            return None  # fall back to the legacy capture path

    def _sck_capture_window(self, window_id: int) -> CaptureResult | None:
        """One window via ScreenCaptureKit, or None to use the legacy path."""
        sck = self._sck()
        if sck is None:
            return None
        try:
            content = self._sck_shareable_content(sck)
            target = next(
                (w for w in content.windows() if int(w.windowID()) == int(window_id)), None
            )
            if target is None:
                return None
            content_filter = sck.SCContentFilter.alloc().initWithDesktopIndependentWindow_(target)
            scale = float(content_filter.pointPixelScale())
            return self._cgimage_to_result(self._sck_screenshot(sck, content_filter), scale=scale)
        except Exception:
            return None  # fall back to the legacy capture path

    @staticmethod
    def _composite_displays(shots) -> CaptureResult:
        """Stitch per-display CGImages into one virtual-desktop image.

        ``shots`` is ``(frame, scale, cg_image)`` per display; frames are in
        points in the top-left-origin global space that both ``SCDisplay.frame``
        and Qt's virtual desktop use. Rendered at the sharpest display's scale
        so no capture is downsampled.
        """
        import Quartz

        left = min(f.origin.x for f, _, _ in shots)
        top = min(f.origin.y for f, _, _ in shots)
        right = max(f.origin.x + f.size.width for f, _, _ in shots)
        bottom = max(f.origin.y + f.size.height for f, _, _ in shots)
        scale = max(s for _, s, _ in shots)
        width = max(1, round((right - left) * scale))
        height = max(1, round((bottom - top) * scale))
        buffer, context = MacScreenCapturer._rgba_context(width, height)
        for frame, _, image in shots:
            # CGBitmapContext has a bottom-left origin; flip each frame's y.
            dest = Quartz.CGRectMake(
                (frame.origin.x - left) * scale,
                (bottom - frame.origin.y - frame.size.height) * scale,
                frame.size.width * scale,
                frame.size.height * scale,
            )
            Quartz.CGContextDrawImage(context, dest, image)
        return CaptureResult(
            width=width, height=height, scale=scale, pixels=bytes(buffer), premultiplied=True
        )

    # --- pixel plumbing -----------------------------------------------------

    @staticmethod
    def _rgba_context(width: int, height: int):
        """A padding-free RGBA bitmap context plus its backing buffer.

        The bitmap context only supports premultiplied alpha, so results built
        from it are flagged premultiplied for the imaging layer.
        """
        import Quartz

        bytes_per_row = width * 4
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
        return buffer, context

    @staticmethod
    def _cgimage_to_result(cg_image, scale: float = 1.0) -> CaptureResult:
        import Quartz

        width = int(Quartz.CGImageGetWidth(cg_image))
        height = int(Quartz.CGImageGetHeight(cg_image))
        buffer, context = MacScreenCapturer._rgba_context(width, height)
        Quartz.CGContextDrawImage(context, Quartz.CGRectMake(0, 0, width, height), cg_image)
        return CaptureResult(
            width=width,
            height=height,
            scale=scale,
            pixels=bytes(buffer),
            premultiplied=True,
        )
