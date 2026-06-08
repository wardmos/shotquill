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

from shotquill.capture.base import CaptureResult, Rect, ScreenCapturer, WindowInfo

if TYPE_CHECKING:
    from PySide6.QtGui import QImage

    from shotquill.ocr.base import TextRecognizer

EXIT_PERMISSION = 3
EXIT_UNSUPPORTED = 4
EXIT_NO_MATCH = 5
EXIT_BLOCKED = 6


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


class CaptureBlocked(HeadlessError):
    """The capture targets an app on the blocklist (or the blocklist is
    unreadable, which fails closed). Refusing is the point — this is the
    privacy feature working, not an error to retry."""

    exit_code = EXIT_BLOCKED


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


def active_blocklist():
    """Load the user's blocklist, failing closed when it cannot be read.

    A missing file is the empty list (the common case, no friction). A
    present-but-corrupt file means the user opted into protection that is now
    broken, so we refuse to capture rather than silently grab something they
    meant to block.
    """
    from shotquill import blocklist as bl

    try:
        return bl.load()
    except bl.BlocklistError as exc:
        raise CaptureBlocked(f"blocklist is unreadable, refusing to capture: {exc}") from exc


def _refuse_if_blocked(window: WindowInfo, blocklist, *, via: str) -> None:
    """Raise :class:`CaptureBlocked` (and audit it) if a rule blocks ``window``."""
    rule = blocklist.match(window)
    if rule is None:
        return
    target = f"{window.owner} — {window.title}" if window.title else window.owner
    from shotquill import audit

    audit.record("capture_blocked", via=via, target=target)
    raise CaptureBlocked(
        f"{window.owner} is on the app blocklist (rule {rule.describe()}); refusing to capture it"
    )


def perform_capture(
    capturer: ScreenCapturer,
    *,
    window_id: int | None = None,
    app: str | None = None,
    title: str | None = None,
    region: Rect | None = None,
    blocklist=None,
    via: str = "cli",
) -> tuple[CaptureResult, str, int]:
    """Dispatch one capture and describe what was actually hit.

    Returns ``(result, target, matched)`` where ``target`` names the real
    capture subject (the audit log records truth, not the request) and
    ``matched`` is the ambiguity count for app/title selection (always 1
    otherwise) so front-ends can warn their own way.

    A window or app capture that lands on the blocklist raises
    :class:`CaptureBlocked`. An empty blocklist (the default) takes the exact
    same path as before — no extra window enumeration, no new failure modes.
    Full-screen and region captures are not refused here; the sensitive window
    is one part of the frame and is redacted instead.
    """
    if blocklist is None:
        blocklist = active_blocklist()

    if window_id is not None:
        if blocklist:
            _refuse_blocked_window_id(capturer, window_id, blocklist, via=via)
        return capturer.capture_window(window_id), f"window {window_id}", 1
    if app:
        window, matched = select_window(capturer.list_windows(), app, title)
        if blocklist:
            _refuse_if_blocked(window, blocklist, via=via)
        result = capturer.capture_window(window.window_id)
        return result, f"{window.owner} — {window.title}", matched
    if region is not None:
        target = f"region {region.x},{region.y},{region.width},{region.height}"
        return capturer.capture_region(region), target, 1
    return capturer.capture_fullscreen(), "fullscreen", 1


def _refuse_blocked_window_id(
    capturer: ScreenCapturer, window_id: int, blocklist, *, via: str
) -> None:
    """Refuse a by-id capture of a blocked window. Needs the window's identity,
    so it looks the id up in the window list; if enumeration is unavailable the
    capture proceeds (nothing to match against)."""
    try:
        windows = capturer.list_windows()
    except CapabilityUnsupported:
        return
    match = next((w for w in windows if w.window_id == window_id), None)
    if match is not None:
        _refuse_if_blocked(match, blocklist, via=via)


def windows_payload(windows: list[WindowInfo]) -> list[dict]:
    """The machine-readable window list shared by ``--json`` and MCP."""
    return [
        {
            "id": w.window_id,
            "owner": w.owner,
            "title": w.title,
            "bundle_id": w.bundle_id,
            "bounds": {
                "x": w.bounds.x,
                "y": w.bounds.y,
                "width": w.bounds.width,
                "height": w.bounds.height,
            },
        }
        for w in windows
    ]


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


def downscale_to_width(image: QImage, max_width: int) -> QImage:
    """Cap the width (keeping aspect), shared by ``--max-width`` and MCP.

    A smaller image is returned untouched — the option means "at most",
    so callers can pass a constant without checking the screen size first.
    """
    if max_width <= 0:
        raise ValueError("max_width must be positive")
    if image.width() <= max_width:
        return image
    from PySide6.QtCore import Qt

    return image.scaledToWidth(max_width, Qt.TransformationMode.SmoothTransformation)


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


def doctor_checks() -> list[dict]:
    """The capability matrix behind ``squill doctor`` and the MCP tool."""
    import platform

    from shotquill import paths

    platform_detail = f"{sys.platform} / Python {platform.python_version()}"
    if sys.platform.startswith("linux"):
        # X11 vs Wayland vs a bare tty decides which capabilities can work,
        # so surface it where troubleshooting starts.
        import os

        platform_detail += f" / session {os.environ.get('XDG_SESSION_TYPE') or 'unknown'}"

    checks: list[dict] = [
        {"capability": "platform", "available": True, "detail": platform_detail},
        {"capability": "audit_log", "available": True, "detail": str(paths.audit_log_path())},
        _check_blocklist(),
    ]

    try:
        capturer = get_capturer()
        backend = type(capturer).__name__
        checks.append({"capability": "capture", "available": True, "detail": backend})
        try:
            capturer.list_windows()
            checks.append({"capability": "list_windows", "available": True, "detail": backend})
        except CapabilityUnsupported as exc:
            checks.append({"capability": "list_windows", "available": False, "detail": exc.reason})
    except CapabilityUnsupported as exc:
        checks.append({"capability": "capture", "available": False, "detail": exc.reason})
        checks.append({"capability": "list_windows", "available": False, "detail": exc.reason})

    if sys.platform == "darwin":
        checks.append(_check_screen_recording())

    try:
        get_recognizer()
        checks.append({"capability": "ocr", "available": True, "detail": "Apple Vision"})
    except CapabilityUnsupported as exc:
        checks.append({"capability": "ocr", "available": False, "detail": exc.reason})

    return checks


def _check_blocklist() -> dict:
    """Report the app blocklist so users can confirm what is protected — and
    catch a corrupt file, which fails closed and would otherwise only surface
    as refused captures."""
    from shotquill import blocklist as bl
    from shotquill import paths

    path = paths.blocklist_path()
    try:
        loaded = bl.load(path)
    except bl.BlocklistError as exc:
        return {"capability": "app_blocklist", "available": False, "detail": f"{path}: {exc}"}
    if not loaded:
        detail = f"no rules ({path})"
    else:
        detail = f"{len(loaded.rules)} rule(s): " + ", ".join(r.describe() for r in loaded.rules)
    return {"capability": "app_blocklist", "available": True, "detail": detail}


def _check_screen_recording() -> dict:  # pragma: no cover - macOS only
    """TCC preflight: a denied grant fails silently at capture time, so the
    doctor surfaces it with a deep link instead of letting agents see black
    frames."""
    try:
        from Quartz import CGPreflightScreenCaptureAccess

        granted = bool(CGPreflightScreenCaptureAccess())
    except Exception as exc:
        return {"capability": "screen_recording", "available": False, "detail": f"probe: {exc}"}
    detail = (
        None
        if granted
        else (
            "grant Screen Recording to the invoking app (e.g. your terminal): "
            "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
        )
    )
    return {"capability": "screen_recording", "available": granted, "detail": detail}
