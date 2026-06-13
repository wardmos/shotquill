# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Windows window enumeration + by-id capture over the Win32 API (the part
``QScreen.grabWindow`` can't do).

``QScreen.grabWindow`` gets pixels but knows nothing about *windows* — their
handles, owners, titles, or where each one sits. Listing them (so an agent can
pick one by id, so the smart-capture overlay can spotlight the hovered window,
and so a blocklisted app can be redacted out of a full-screen grab) needs the
window list the desktop window manager keeps, read straight off ``user32``.

The split here mirrors ``x11.py`` and ``redact.py``: the part that calls into
Win32 is a thin, platform-only shim (``# pragma: no cover`` — it needs a real
Windows session, absent under the offscreen test platform); the decisions —
what to skip, how to order, how a raw window becomes a :class:`WindowInfo` —
live in the pure :func:`windows_from_raw` and are unit-tested with plain data.

``GetWindowRect`` reports geometry in *physical* pixels with a top-left origin;
:func:`to_logical_bounds` rescales it to the *logical* points the rest of the
app (and ``WindowInfo.bounds``) speaks, so blocklist redaction and the overlay
stay aligned under display scaling, exactly as the X11 backend does.

The owning app's identity is its executable basename (e.g. ``chrome.exe``).
Windows issues no per-app bundle id, and a friendly product name lives in
per-file version resources that are localizable and optional; the executable
name is stable and app-set, which is sufficient for the blocklist's threat
model (an over-eager or prompt-injected agent, not an adversary running code as
the user, who could rename their own binary only to *avoid* being blocked,
never to leak more) — the same trade-off ``x11.py`` documents for WM_CLASS.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace

from shotquill.capture.base import CaptureResult, Rect, WindowInfo
from shotquill.capture.qtgrab import QtGrabCapturer, _qimage_to_result
from shotquill.headless import CapabilityUnsupported


@dataclass(frozen=True)
class RawWindow:
    """One top-level window as read off Win32, before any filtering.

    ``process_name`` is the owning executable's basename (the blocklist key on
    Windows). ``cloaked`` is the DWM "cloaked" state — UWP/suspended windows the
    shell still lists but that aren't really on screen. ``tool_window`` is the
    ``WS_EX_TOOLWINDOW`` extended style: floating palettes and helper windows
    that, by convention (the same rule Alt-Tab uses), aren't capture targets.
    """

    hwnd: int
    title: str
    process_name: str | None
    pid: int | None
    bounds: Rect
    visible: bool
    cloaked: bool
    tool_window: bool


def windows_from_raw(raws: list[RawWindow], own_pid: int) -> list[WindowInfo]:
    """Filter raw Win32 windows into the capture interface's list.

    ``EnumWindows`` already yields top-level windows front-most first (Z-order,
    top to bottom), so unlike the X11 stacking list this needs no reordering.
    Drops our own windows (so the overlay never offers itself as a target),
    invisible / DWM-cloaked windows, tool windows, degenerate sizes, and
    title-less windows — the last because Windows keeps many visible top-level
    windows with no caption (shell hosts, background surfaces) that are not
    things a user would pick, the same heuristic the Alt-Tab list applies.
    """
    out: list[WindowInfo] = []
    for raw in raws:
        if not raw.visible or raw.cloaked or raw.tool_window:
            continue
        if raw.pid is not None and raw.pid == own_pid:
            continue
        if raw.bounds.width < 1 or raw.bounds.height < 1:
            continue
        if not raw.title:
            continue
        out.append(
            WindowInfo(
                window_id=raw.hwnd,
                owner=raw.process_name or "",
                title=raw.title,
                bounds=raw.bounds,
                bundle_id=raw.process_name or None,
            )
        )
    return out


def to_logical_bounds(windows: list[WindowInfo], dpr: float) -> list[WindowInfo]:
    """Rescale Win32 *physical*-pixel bounds to the *logical* points the rest of
    the app speaks (Qt's virtual-desktop geometry).

    On a standard-DPI desktop ``dpr`` is 1.0 and this is a no-op; under display
    scaling it keeps a blocklist redaction block aligned with the window it must
    cover (a misplaced block would leave the sensitive pixels in the saved
    image). Edges round outward so the logical rect never shrinks inside the
    real window. Best-effort under a *mixed*-DPI multi-monitor desktop — like
    the capture itself it assumes one ratio — but the dominant single-ratio case
    (including plain 1.0) is exact. Mirrors ``x11.to_logical_bounds``.
    """
    if dpr == 1.0:
        return windows
    import math

    rescaled = []
    for w in windows:
        b = w.bounds
        x0 = math.floor(b.x / dpr)
        y0 = math.floor(b.y / dpr)
        x1 = math.ceil((b.x + b.width) / dpr)
        y1 = math.ceil((b.y + b.height) / dpr)
        rescaled.append(replace(w, bounds=Rect(x0, y0, x1 - x0, y1 - y0)))
    return rescaled


def list_windows() -> list[WindowInfo]:
    """On-screen application windows, front-most first, in *physical* pixels.

    The Qt backend (:class:`WindowsScreenCapturer`) wraps this to rescale bounds
    to logical points — callers that need the documented logical geometry should
    go through the backend, not here directly.

    Raises :class:`CapabilityUnsupported` when the platform can't answer (the
    ``ctypes``/Win32 surface is unreachable), so the caller emits a typed
    "unsupported" signal instead of an empty list a user would misread as
    "nothing on screen".
    """
    raws = _enumerate_raw()
    return windows_from_raw(raws, os.getpid())


class WindowsScreenCapturer(QtGrabCapturer):
    """Qt-grab capture plus Win32 window enumeration / by-id capture.

    Full-screen and region capture are inherited unchanged from
    :class:`QtGrabCapturer` (``QScreen.grabWindow`` covers them on Windows with
    no extra dependency); this adds the window list and by-id grab that need the
    Win32 API ``grabWindow`` alone can't provide.
    """

    def list_windows(self) -> list[WindowInfo]:
        # Win32 geometry is physical pixels; rescale to the logical points the
        # overlay and blocklist redaction expect, using the same device-pixel
        # ratio the capture path applies so the two stay in lockstep.
        return to_logical_bounds(list_windows(), self._capture_dpr())

    def capture_window(self, window_id: int) -> CaptureResult:
        # Find the window in the live list so an unknown/closed handle fails
        # clearly and we have its absolute bounds for the result's origin; then
        # let Qt pull the window's own pixels by HWND (a window id *is* the HWND
        # on Windows, which is what QScreen.grabWindow's WId expects).
        window = next((w for w in list_windows() if w.window_id == window_id), None)
        if window is None:
            raise RuntimeError(f"window {window_id} is not on screen")
        return self._grab_window_id(window_id, window.bounds)

    @staticmethod
    def _grab_window_id(window_id: int, bounds: Rect) -> CaptureResult:
        """Grab one window's pixels by HWND, tagged with its absolute origin.

        ``bounds`` is the window's screen rectangle (already logical points), so
        the result's origin lines up with full-screen and region grabs for
        redaction maths. Must run on Qt's GUI thread — the CLI/MCP
        ``--window``/``--app`` paths and the overlay's click-to-capture are all
        there; the overlay's off-thread hover preview already treats any failure
        as "no preview" and keeps the frozen screenshot.
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


# --- Win32 shim: needs a real Windows session --------------------------------

# Extended window style + DWM attribute constants (winuser.h / dwmapi.h).
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_DWMWA_CLOAKED = 14
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _enumerate_raw() -> list[RawWindow]:  # pragma: no cover - needs a real Windows session
    """Read every top-level window off ``user32`` as :class:`RawWindow` records.

    ``EnumWindows`` walks the desktop's top-level windows in Z-order (front-most
    first). Each window is queried defensively: one that closes mid-enumeration
    makes a call fail, which is skipped rather than aborting the whole list.
    """
    import ctypes
    from ctypes import wintypes

    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except (OSError, AttributeError) as exc:  # not really Windows / no WinDLL
        raise CapabilityUnsupported(
            "list_windows", f"the Win32 window API is unavailable: {exc}"
        ) from exc

    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    raws: list[RawWindow] = []

    def _callback(hwnd, _lparam):
        try:
            raws.append(_read_window(hwnd, user32, dwmapi, kernel32))
        except OSError:
            pass  # window vanished mid-read; skip it, keep enumerating
        return True

    if not user32.EnumWindows(enum_proc(_callback), 0):
        err = ctypes.get_last_error()
        # ERROR_SUCCESS (0) can accompany an early-stopped enumeration; only a
        # real error code is a failure worth surfacing.
        if err:
            raise CapabilityUnsupported(
                "list_windows", f"EnumWindows failed (error {err})"
            )
    return raws


def _read_window(hwnd, user32, dwmapi, kernel32) -> RawWindow:  # pragma: no cover - needs Windows
    """One window's fields, read defensively off Win32."""
    import ctypes
    from ctypes import wintypes

    visible = bool(user32.IsWindowVisible(hwnd))

    length = user32.GetWindowTextLengthW(hwnd)
    title = ""
    if length:
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    bounds = Rect(
        x=int(rect.left),
        y=int(rect.top),
        width=int(rect.right - rect.left),
        height=int(rect.bottom - rect.top),
    )

    ex_style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
    tool_window = bool(ex_style & _WS_EX_TOOLWINDOW)

    cloaked = ctypes.c_int(0)
    dwmapi.DwmGetWindowAttribute(
        hwnd, _DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
    )

    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    pid_value = int(pid.value) or None

    return RawWindow(
        hwnd=int(hwnd),
        title=title,
        process_name=_process_name(pid_value, kernel32),
        pid=pid_value,
        bounds=bounds,
        visible=visible,
        cloaked=bool(cloaked.value),
        tool_window=tool_window,
    )


def _process_name(pid, kernel32) -> str | None:  # pragma: no cover - needs a real Windows session
    """The owning process's executable basename, or None when it can't be read.

    A foreign process may deny access (e.g. an elevated app); that is expected
    and yields ``None`` — the window still lists, just without an owner/blocklist
    key, rather than failing the whole enumeration.
    """
    import ctypes
    from ctypes import wintypes

    if not pid:
        return None
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(260)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return os.path.basename(buf.value) or None
    finally:
        kernel32.CloseHandle(handle)
