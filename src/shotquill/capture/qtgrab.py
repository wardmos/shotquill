# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Qt-based screen capture: the thin Linux/X11 slice.

``QScreen.grabWindow`` needs nothing beyond PySide6 (already a dependency),
which makes full-screen and region capture on X11 nearly free — enough for
the headless CLI/MCP path and for real end-to-end tests under Xvfb. The
expensive parts of Linux support are deliberately *not* here:

- Wayland refuses out-of-band grabs by design; that is served by the
  xdg-desktop-portal backend (:mod:`shotquill.capture.wayland`), which the
  capturer factory selects on a Wayland session. This X11 slice is not chosen
  there, and refuses up front if constructed directly so the failure points at
  the portal path rather than handing back a blank frame.
- X11 window enumeration/picking is also deferred — ``list_windows`` and
  ``capture_window`` raise ``CapabilityUnsupported`` so agents get a typed
  signal (exit code 4) instead of an empty list they would misread as
  "no windows on screen".
"""

from __future__ import annotations

import os
import sys

from shotquill.capture.base import CaptureResult, Rect, ScreenCapturer, WindowInfo
from shotquill.headless import CapabilityUnsupported

# The QGuiApplication we create on demand; module-level so it outlives calls.
_app = None


def _ensure_gui_session() -> None:
    """Create a QGuiApplication if needed; refuse sessions Qt cannot grab."""
    global _app
    from PySide6.QtGui import QGuiApplication

    if QGuiApplication.instance() is not None:
        return
    if sys.platform.startswith("linux") and not os.environ.get("QT_QPA_PLATFORM"):
        session = os.environ.get("XDG_SESSION_TYPE", "")
        if session == "wayland":
            raise CapabilityUnsupported(
                "capture",
                "Wayland blocks out-of-band grabs; use the xdg-desktop-portal backend "
                "(shotquill.capture.wayland.PortalScreenCapturer)",
            )
        if not os.environ.get("DISPLAY"):
            raise CapabilityUnsupported("capture", "no display session (DISPLAY is unset)")
    _app = QGuiApplication([])


class QtGrabCapturer(ScreenCapturer):
    """Full-screen / region capture via QScreen.grabWindow (X11 and tests)."""

    def __init__(self, include_cursor: bool = False) -> None:
        # Qt grabs never include the pointer; keep the attribute for the
        # ScreenCapturer contract (include_cursor is documented best-effort).
        self.include_cursor = include_cursor
        _ensure_gui_session()

    def capture_fullscreen(self, exclude_window_ids: frozenset[int] = frozenset()) -> CaptureResult:
        # This backend cannot enumerate windows, so it never has ids to exclude
        # (the caller redacts via rectangles where it does); accept the argument
        # for the interface and ignore it.
        canvas, virtual, dpr = self._grab_virtual_desktop()
        return _qimage_to_result(canvas, dpr, origin=(virtual.x(), virtual.y()))

    def capture_region(self, region: Rect) -> CaptureResult:
        from PySide6.QtCore import QRect

        canvas, virtual, dpr = self._grab_virtual_desktop()
        crop = QRect(
            int((region.x - virtual.x()) * dpr),
            int((region.y - virtual.y()) * dpr),
            int(region.width * dpr),
            int(region.height * dpr),
        )
        if not crop.intersects(canvas.rect()):
            raise ValueError(f"region {region} is outside the virtual desktop")
        return _qimage_to_result(
            canvas.copy(crop.intersected(canvas.rect())), dpr, origin=(region.x, region.y)
        )

    def list_windows(self) -> list[WindowInfo]:
        raise CapabilityUnsupported(
            "list_windows", "window enumeration is not implemented on this backend yet"
        )

    def capture_window(self, window_id: int) -> CaptureResult:
        raise CapabilityUnsupported(
            "capture_window", "window capture is not implemented on this backend yet"
        )

    @staticmethod
    def _grab_virtual_desktop():
        """Composite every screen onto one canvas in virtual-desktop space."""
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QGuiApplication, QImage, QPainter

        screens = QGuiApplication.screens()
        if not screens:
            raise CapabilityUnsupported("capture", "Qt reports no screens")
        virtual = screens[0].virtualGeometry()
        dpr = max(s.devicePixelRatio() for s in screens)
        canvas = QImage(
            int(virtual.width() * dpr),
            int(virtual.height() * dpr),
            QImage.Format.Format_RGBA8888,
        )
        canvas.fill(0)
        painter = QPainter(canvas)
        for screen in screens:
            geometry = screen.geometry()
            target = QRect(
                int((geometry.x() - virtual.x()) * dpr),
                int((geometry.y() - virtual.y()) * dpr),
                int(geometry.width() * dpr),
                int(geometry.height() * dpr),
            )
            painter.drawImage(target, screen.grabWindow(0).toImage())
        painter.end()
        return canvas, virtual, dpr


def _qimage_to_result(image, scale: float, origin: tuple[int, int] = (0, 0)) -> CaptureResult:
    """Flatten a QImage into the raw-RGBA CaptureResult the pipeline expects."""
    from PySide6.QtGui import QImage

    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    raw = bytes(image.constBits())
    row = image.width() * 4
    stride = image.bytesPerLine()
    if stride != row:
        # Qt may pad scanlines; CaptureResult pixels are tightly packed.
        raw = b"".join(raw[y * stride : y * stride + row] for y in range(image.height()))
    return CaptureResult(
        width=image.width(),
        height=image.height(),
        scale=float(scale),
        pixels=raw,
        origin_x=int(origin[0]),
        origin_y=int(origin[1]),
    )
