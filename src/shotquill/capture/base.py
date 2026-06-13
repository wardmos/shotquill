# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Abstract interface for screen capture.

Platform implementations (e.g. ``macos.py``) return raw pixels at native
(Retina) resolution; higher layers handle scaling for display and output.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    """A rectangle in logical (point) screen coordinates."""

    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class CaptureResult:
    """A captured image: RGBA pixels plus geometry and the Retina scale factor.

    ``premultiplied`` is True when the alpha channel is premultiplied into the
    colour channels (as macOS window captures are); the imaging layer picks the
    matching QImage format so transparent window corners render correctly.

    ``origin_x`` / ``origin_y`` are the logical (point) coordinates of the
    image's top-left in the same space as ``WindowInfo.bounds`` — ``(0, 0)`` for
    a single display or a primary at the origin, but the real (possibly
    negative) origin for a multi-monitor full-screen grab, so blocklist
    redaction maps window bounds onto the right pixels.

    ``excluded_window_ids`` are the window ids the capture itself kept out of the
    image (macOS ScreenCaptureKit can omit specific windows, so a blocklisted
    app is simply absent — what was behind it shows through, and windows on top
    of it stay intact). The shared layer redacts only the blocklisted windows
    *not* in this set, so it never paints a solid block over a window the
    capture already excluded.
    """

    width: int
    height: int
    scale: float
    pixels: bytes
    premultiplied: bool = False
    origin_x: int = 0
    origin_y: int = 0
    excluded_window_ids: frozenset[int] = frozenset()


@dataclass(frozen=True)
class DisplayInfo:
    """A physical display (monitor) and where it sits on the virtual desktop.

    ``index`` is the stable selection handle the CLI/MCP ``display`` option
    takes — primary display first, the rest in the platform's enumeration
    order. ``bounds`` is in logical (point) coordinates in the same space as
    ``WindowInfo.bounds``, so a display capture is exactly a region capture of
    its bounds. ``scale`` is the display's pixel ratio (best effort, 1.0 where
    the platform does not expose it).
    """

    index: int
    name: str
    bounds: Rect
    scale: float = 1.0
    primary: bool = False


@dataclass(frozen=True)
class WindowInfo:
    """An on-screen application window: its id, owner app, title, and bounds.

    ``bounds`` is in logical (point) screen coordinates with a top-left origin,
    matching Qt's virtual-desktop geometry.

    ``bundle_id`` is the owning app's stable identifier (e.g.
    ``com.1password.1password`` on macOS), resolved from the window's process
    where the platform exposes it and ``None`` otherwise. It is the robust key
    the app blocklist matches on — display names are localizable and a window's
    title is attacker-controlled, but the bundle id is not.

    On X11 there is no OS-issued bundle id, so this carries the window's WM_CLASS
    instead — stable and the conventional app identity, but *app-set* rather than
    OS-issued. That is sufficient for the blocklist's threat model (an
    over-eager or prompt-injected agent, not an adversary running code as the
    user, who could spoof WM_CLASS but only to avoid being blocked, never to
    leak more).
    """

    window_id: int
    owner: str
    title: str
    bounds: Rect
    bundle_id: str | None = None


class ScreenCapturer(ABC):
    """Captures screen pixels at native resolution.

    ``include_cursor`` controls whether the mouse pointer is composited into
    captures (best effort — not every backend can honor it). Off by default.
    """

    include_cursor: bool = False

    @abstractmethod
    def capture_fullscreen(self, exclude_window_ids: frozenset[int] = frozenset()) -> CaptureResult:
        """Capture all displays composited into a single image.

        ``exclude_window_ids`` names windows to keep out of the capture where the
        backend can (macOS ScreenCaptureKit); the returned
        ``CaptureResult.excluded_window_ids`` reports which were actually
        omitted, so the caller can redact the rest some other way."""

    @abstractmethod
    def capture_region(self, region: Rect) -> CaptureResult:
        """Capture a sub-region of the virtual desktop."""

    @abstractmethod
    def list_windows(self) -> list[WindowInfo]:
        """List on-screen application windows, front-most first."""

    @abstractmethod
    def capture_window(self, window_id: int) -> CaptureResult:
        """Capture a single window's pixels at native resolution.

        ``list_displays`` below is deliberately *not* abstract: the Qt default
        works for every current backend, and existing test fakes satisfy the
        interface without knowing about displays."""

    def list_displays(self) -> list[DisplayInfo]:
        """List displays, primary first; a display capture is a region capture
        of the returned bounds.

        Default implementation reads Qt's screen list, which every current
        backend can reach (the Linux backends already own a ``QGuiApplication``;
        one is created here if needed). Backends with a more native source can
        override.
        """
        from PySide6.QtGui import QGuiApplication

        if QGuiApplication.instance() is None:
            # Same on-demand app the Linux backends create; Qt keeps its own
            # reference once constructed.
            QGuiApplication([])
        screens = QGuiApplication.screens()
        if not screens:
            from shotquill.headless import CapabilityUnsupported

            raise CapabilityUnsupported("displays", "Qt reports no screens")
        primary = QGuiApplication.primaryScreen()
        ordered = [primary] + [s for s in screens if s is not primary] if primary else screens
        displays = []
        for i, screen in enumerate(ordered):
            geometry = screen.geometry()
            displays.append(
                DisplayInfo(
                    index=i,
                    name=screen.name() or f"display {i}",
                    bounds=Rect(
                        x=geometry.x(),
                        y=geometry.y(),
                        width=geometry.width(),
                        height=geometry.height(),
                    ),
                    scale=float(screen.devicePixelRatio()),
                    primary=screen is primary,
                )
            )
        return displays
