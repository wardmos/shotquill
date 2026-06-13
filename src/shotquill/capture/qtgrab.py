# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Qt-based screen capture: the thin Linux/X11 slice.

``QScreen.grabWindow`` needs nothing beyond PySide6 (already a dependency),
which makes full-screen and region capture on X11 nearly free — enough for
the headless CLI/MCP path and for real end-to-end tests under Xvfb.

Window enumeration and by-id capture need more than pixels (the window
manager's EWMH properties), so the enumeration lives in ``x11.py`` and is
delegated to from here. Where it can't be answered — no EWMH window manager,
no reachable server, or ``python-xlib`` absent — it raises
``CapabilityUnsupported`` so agents get a typed signal (exit code 4) instead
of an empty list they would misread as "no windows on screen".

Wayland is different again: the compositor refuses out-of-band grabs *and*
window enumeration by design, so that path is served by the xdg-desktop-portal
backend (:mod:`shotquill.capture.wayland`), which the capturer factory selects
on a Wayland session. This X11 slice is not chosen there, and refuses up front
if constructed directly so the failure points at the portal path rather than
handing back a blank frame.
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
        # EWMH enumeration over python-xlib; raises CapabilityUnsupported when
        # there is no window manager / server / library to read it from. X11
        # geometry is physical pixels; rescale to the logical points the
        # overlay and blocklist redaction expect, using the same device-pixel
        # ratio the capture path applies so the two stay in lockstep.
        from shotquill.capture import x11

        return x11.to_logical_bounds(x11.list_windows(), self._capture_dpr())

    def capture_window(self, window_id: int) -> CaptureResult:
        # Find the window in the live list so an unknown/closed id fails clearly
        # and we have its absolute bounds for the result's origin; then let Qt
        # pull the window's own pixels by id.
        from shotquill.capture import x11

        window = next((w for w in x11.list_windows() if w.window_id == window_id), None)
        if window is None:
            raise RuntimeError(f"window {window_id} is not on screen")
        return self._grab_window_id(window_id, window.bounds)

    @staticmethod
    def _grab_window_id(window_id: int, bounds: Rect) -> CaptureResult:
        """Grab one X window's pixels by id, tagged with its absolute origin.

        Best-effort on X11: without a compositor the server has no off-screen
        copy of an obscured window, so a covered region may read stale — the
        common case (a visible window) is exact. ``bounds`` is the window's
        root-space rectangle, so the result's origin lines up with full-screen
        and region grabs for redaction maths.

        Unlike the macOS backend (which talks to the window server directly and
        is thread-safe), this goes through Qt, so it must run on the GUI thread.
        The CLI/MCP ``--window``/``--app`` paths and the overlay's click-to-
        capture are all on that thread; the overlay's off-thread hover *preview*
        is the one best-effort caller, and it already treats any failure as "no
        preview" (keeping the frozen screenshot).
        """
        from PySide6.QtGui import QGuiApplication

        screens = QGuiApplication.screens()
        if not screens:
            raise CapabilityUnsupported("capture_window", "Qt reports no screens")
        pixmap = screens[0].grabWindow(window_id)
        if pixmap.isNull():
            raise RuntimeError(f"window {window_id} could not be captured")
        dpr = pixmap.devicePixelRatio() or 1.0
        return _qimage_to_result(pixmap.toImage(), dpr, origin=(bounds.x, bounds.y))

    @staticmethod
    def _capture_dpr() -> float:
        """The device-pixel ratio the capture canvas uses — the single source of
        truth shared with ``list_windows`` so window bounds rescale to exactly
        the logical space the captured pixels are addressed in."""
        from PySide6.QtGui import QGuiApplication

        screens = QGuiApplication.screens()
        if not screens:
            raise CapabilityUnsupported("capture", "Qt reports no screens")
        return max(s.devicePixelRatio() for s in screens)

    @staticmethod
    def _grab_virtual_desktop():
        """Composite every screen onto one canvas in virtual-desktop space."""
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QGuiApplication, QImage, QPainter

        screens = QGuiApplication.screens()
        if not screens:
            raise CapabilityUnsupported("capture", "Qt reports no screens")
        virtual = screens[0].virtualGeometry()
        dpr = QtGrabCapturer._capture_dpr()
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
