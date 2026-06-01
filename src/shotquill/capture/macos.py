# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""macOS screen capture backed by ``mss`` (CoreGraphics under the hood).

Captures at native (Retina) pixel resolution. Pixels are normalized to RGBA so
downstream code (PIL saver, Qt clipboard) does not need to know about mss.
"""

from __future__ import annotations

import mss
from PIL import Image

from shotquill.capture.base import CaptureResult, Rect, ScreenCapturer


class MacScreenCapturer(ScreenCapturer):
    def capture_fullscreen(self) -> CaptureResult:
        with mss.mss() as sct:
            # monitors[0] is the virtual screen spanning every display.
            shot = sct.grab(sct.monitors[0])
        return self._to_result(shot)

    def capture_region(self, region: Rect) -> CaptureResult:
        bbox = {
            "left": region.x,
            "top": region.y,
            "width": region.width,
            "height": region.height,
        }
        with mss.mss() as sct:
            shot = sct.grab(bbox)
        return self._to_result(shot)

    @staticmethod
    def _to_result(shot: mss.screenshot.ScreenShot) -> CaptureResult:
        width, height = shot.size
        # mss yields raw BGRA; let PIL reorder it to RGBA in one pass.
        rgba = Image.frombytes("RGBA", shot.size, bytes(shot.bgra), "raw", "BGRA").tobytes()
        # scale is refined in Phase 2 via QScreen.devicePixelRatio; the saved
        # image is already at native resolution regardless.
        return CaptureResult(width=width, height=height, scale=1.0, pixels=rgba)
