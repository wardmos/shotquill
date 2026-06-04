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
    """

    width: int
    height: int
    scale: float
    pixels: bytes
    premultiplied: bool = False


@dataclass(frozen=True)
class WindowInfo:
    """An on-screen application window: its id, owner app, title, and bounds.

    ``bounds`` is in logical (point) screen coordinates with a top-left origin,
    matching Qt's virtual-desktop geometry.
    """

    window_id: int
    owner: str
    title: str
    bounds: Rect


class ScreenCapturer(ABC):
    """Captures screen pixels at native resolution.

    ``include_cursor`` controls whether the mouse pointer is composited into
    captures (best effort — not every backend can honor it). Off by default.
    """

    include_cursor: bool = False

    @abstractmethod
    def capture_fullscreen(self) -> CaptureResult:
        """Capture all displays composited into a single image."""

    @abstractmethod
    def capture_region(self, region: Rect) -> CaptureResult:
        """Capture a sub-region of the virtual desktop."""

    @abstractmethod
    def list_windows(self) -> list[WindowInfo]:
        """List on-screen application windows, front-most first."""

    @abstractmethod
    def capture_window(self, window_id: int) -> CaptureResult:
        """Capture a single window's pixels at native resolution."""
