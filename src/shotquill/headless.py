# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Headless capture operations shared by the CLI and the (future) MCP server.

This is the seam the two front-ends meet at: all real logic lives here as
plain functions over the ``ScreenCapturer`` / ``TextRecognizer`` abstractions,
so the CLI stays a thin argparse layer and MCP can later wrap the same calls
instead of shelling out.

Errors are typed and carry the documented CLI exit codes, because agents
branch on them: 3 permission, 4 capability unavailable on this platform or
session, 5 no window matched.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from shotquill.capture.base import Rect, ScreenCapturer, WindowInfo

if TYPE_CHECKING:
    from PySide6.QtGui import QImage

    from shotquill.ocr.base import TextRecognizer

EXIT_PERMISSION = 3
EXIT_UNSUPPORTED = 4
EXIT_NO_MATCH = 5


class HeadlessError(Exception):
    """Base for typed headless failures; ``exit_code`` is the CLI contract."""

    exit_code = 1


class CapturePermissionError(HeadlessError):
    exit_code = EXIT_PERMISSION


class CapabilityUnsupported(HeadlessError):
    """The capability does not exist on this platform/session (e.g. listing
    windows under Wayland) — distinct from a transient failure, so agents can
    stop retrying and pick another path."""

    exit_code = EXIT_UNSUPPORTED

    def __init__(self, capability: str, reason: str) -> None:
        super().__init__(f"{capability} is not available: {reason}")
        self.capability = capability
        self.reason = reason


class WindowNotFound(HeadlessError):
    exit_code = EXIT_NO_MATCH


def get_capturer(include_cursor: bool = False) -> ScreenCapturer:
    """Pick the platform capture backend (the CLI/MCP factory seam)."""
    if sys.platform == "darwin":
        from shotquill.capture.macos import MacScreenCapturer

        return MacScreenCapturer(include_cursor=include_cursor)
    if sys.platform.startswith("linux"):
        from shotquill.capture.qtgrab import QtGrabCapturer

        return QtGrabCapturer(include_cursor=include_cursor)
    raise CapabilityUnsupported("capture", f"no backend for platform {sys.platform!r}")


def get_recognizer() -> TextRecognizer:
    if sys.platform == "darwin":
        from shotquill.ocr.macos import VisionTextRecognizer

        return VisionTextRecognizer()
    raise CapabilityUnsupported("ocr", "on-device OCR currently requires macOS Vision")


def select_window(
    windows: list[WindowInfo], app: str, title: str | None = None
) -> tuple[WindowInfo, int]:
    """Pick a window by case-insensitive substring on owner (and title).

    Returns the front-most match plus the total match count — ``list_windows``
    is contractually front-most-first, and capture is read-only/retryable, so
    on ambiguity we take the front window and let the caller warn rather than
    hard-fail (a hard error would cancel out the one-step convenience).
    """
    app_needle = app.casefold()
    title_needle = title.casefold() if title else None
    matches = [
        w
        for w in windows
        if app_needle in w.owner.casefold()
        and (title_needle is None or title_needle in w.title.casefold())
    ]
    if not matches:
        wanted = f"app {app!r}" + (f" title {title!r}" if title else "")
        raise WindowNotFound(f"no on-screen window matches {wanted}")
    return matches[0], len(matches)


def parse_region(text: str) -> Rect:
    """Parse the ``x,y,w,h`` syntax (four integers, logical coordinates)."""
    parts = text.split(",")
    if len(parts) != 4:
        raise ValueError(f"region must be x,y,w,h — got {text!r}")
    try:
        x, y, w, h = (int(p.strip()) for p in parts)
    except ValueError:
        raise ValueError(f"region must be four integers x,y,w,h — got {text!r}") from None
    if w <= 0 or h <= 0:
        raise ValueError(f"region width/height must be positive — got {text!r}")
    return Rect(x=x, y=y, width=w, height=h)


def encode_qimage(image: QImage, image_format: str = "png") -> bytes:
    """Serialize a QImage to PNG/JPEG bytes (for ``-o -`` and MCP payloads)."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QImage as _QImage

    fmt = "jpg" if image_format.lower() in ("jpg", "jpeg") else "png"
    if fmt == "jpg":
        # JPEG has no alpha; convert explicitly so the result is deterministic.
        image = image.convertToFormat(_QImage.Format.Format_RGB888)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, fmt.upper()):
        raise OSError(f"failed to encode image as {fmt}")
    return bytes(data)
