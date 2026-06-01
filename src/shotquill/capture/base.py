# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Abstract interface for screen capture.

Platform implementations (e.g. ``macos.py``) return raw pixels at native
(Retina) resolution; higher layers handle scaling for display and output.
"""

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
    """A captured image: RGBA pixels plus geometry and the Retina scale factor."""

    width: int
    height: int
    scale: float
    pixels: bytes


class ScreenCapturer(ABC):
    """Captures screen pixels at native resolution."""

    @abstractmethod
    def capture_fullscreen(self) -> CaptureResult:
        """Capture all displays composited into a single image."""

    @abstractmethod
    def capture_region(self, region: Rect) -> CaptureResult:
        """Capture a sub-region of the virtual desktop."""
