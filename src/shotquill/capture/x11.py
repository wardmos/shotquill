# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""X11 window enumeration over EWMH (the part QScreen.grabWindow can't do).

``QScreen.grabWindow`` gets pixels but knows nothing about *windows* — their
ids, owners, titles, or where each one sits. Listing them (so an agent can pick
one by id, so the smart-capture overlay can spotlight the hovered window, and so
a blocklisted app can be redacted out of a full-screen grab) needs the window
manager's EWMH properties, read straight off the X server.

The split here mirrors ``redact.py``: the part that talks to X11 is a thin,
platform-only shim (``# pragma: no cover`` — it needs a live server, the
``python-xlib`` package, and a running EWMH window manager, none of which exist
under the offscreen/Xvfb test platforms); the decisions — what to skip, how to
order, how raw X properties become a :class:`WindowInfo` — live in the pure
:func:`windows_from_raw` and are unit-tested with plain data.

The X protocol reports geometry in *physical* pixels with a top-left origin;
:func:`to_logical_bounds` rescales it to the *logical* points the rest of the
app (and ``WindowInfo.bounds``) speaks, so blocklist redaction and the overlay
stay aligned under display scaling. Wayland is handled separately — the
compositor forbids an app from enumerating other apps' windows at all (see
``wayland.py``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace

from shotquill.capture.base import Rect, WindowInfo
from shotquill.headless import CapabilityUnsupported

# Window types that are chrome, not application windows — the X11 analogue of
# the macOS "layer 0 only" filter (the panel, the desktop/wallpaper, the dock).
# Anything else (NORMAL, DIALOG, UTILITY, or an untyped legacy window) is a
# candidate the user might want to capture.
_SKIP_WINDOW_TYPES = frozenset(
    {
        "_NET_WM_WINDOW_TYPE_DESKTOP",
        "_NET_WM_WINDOW_TYPE_DOCK",
    }
)


@dataclass(frozen=True)
class RawWindow:
    """One window as read off the X server, before any filtering.

    The fields are exactly what EWMH/ICCCM expose: ``wm_class`` is the WM_CLASS
    *class* (e.g. ``firefox``, ``1Password``) — the stable, app-set identity
    that doubles as the blocklist key on X11; ``window_type`` is the resolved
    ``_NET_WM_WINDOW_TYPE`` atom name (``None`` for legacy untyped windows);
    ``viewable`` is false for minimized/unmapped windows the WM still lists.
    """

    window_id: int
    title: str
    wm_class: str | None
    pid: int | None
    bounds: Rect
    viewable: bool
    window_type: str | None


def windows_from_raw(
    raws: list[RawWindow], own_pid: int, *, from_stacking: bool
) -> list[WindowInfo]:
    """Filter and order raw X windows into the capture interface's list.

    Drops our own windows (so the overlay never offers itself as a target),
    chrome (desktop/dock), minimized windows, and degenerate sizes — mirroring
    the macOS backend. ``from_stacking`` is true when the source was
    ``_NET_CLIENT_LIST_STACKING`` (bottom-to-top), which is reversed to the
    front-most-first order the interface promises; the fallback
    ``_NET_CLIENT_LIST`` carries no stacking and is left in its given order.
    """
    out: list[WindowInfo] = []
    for raw in raws:
        if not raw.viewable:
            continue
        if raw.pid is not None and raw.pid == own_pid:
            continue
        if raw.window_type in _SKIP_WINDOW_TYPES:
            continue
        if raw.bounds.width < 1 or raw.bounds.height < 1:
            continue
        out.append(
            WindowInfo(
                window_id=raw.window_id,
                # X11 has no per-app display name distinct from WM_CLASS; the
                # class is what users recognize and what the blocklist matches.
                owner=raw.wm_class or "",
                title=raw.title,
                bounds=raw.bounds,
                bundle_id=raw.wm_class or None,
            )
        )
    if from_stacking:
        out.reverse()
    return out


def to_logical_bounds(windows: list[WindowInfo], dpr: float) -> list[WindowInfo]:
    """Rescale X11 *physical*-pixel bounds to the *logical* points the rest of
    the app speaks (Qt's virtual-desktop geometry, what ``WindowInfo.bounds`` is
    contractually in).

    The X protocol reports geometry in device pixels, but the capture origin,
    the overlay, and — critically — blocklist redaction all work in logical
    points scaled by the capture's device-pixel ratio. On a standard-DPI desktop
    ``dpr`` is 1.0 and this is a no-op; under display scaling it is what keeps a
    blocklist redaction block aligned with the window it must cover (a misplaced
    block would leave the sensitive pixels in the saved image). Edges round
    outward so the logical rect never shrinks back inside the real window.

    Best-effort under a *mixed*-DPI multi-monitor desktop: like the capture
    itself it assumes one ratio, so a window on a differently scaled monitor can
    be off — the dominant single-ratio case (including plain 1.0) is exact.
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
    """On-screen application windows, front-most first, in *physical* X11 pixels.

    The Qt backend wraps this to rescale bounds to logical points (see
    :func:`to_logical_bounds`) — callers that need the documented logical
    geometry should go through the backend, not here directly.

    Raises :class:`CapabilityUnsupported` when the platform can't answer —
    ``python-xlib`` not installed, no reachable X server, or no EWMH-compliant
    window manager running — so the caller emits a typed "unsupported" signal
    instead of an empty list a user would misread as "nothing on screen".
    """
    display = _connect()
    try:
        raws, from_stacking = _read_raw(display)
    except CapabilityUnsupported:
        raise
    except Exception as exc:  # noqa: BLE001 - any X protocol/connection fault
        # A server that drops mid-read (or any unexpected Xlib fault) is still
        # "can't enumerate" — surface it as the typed unsupported signal callers
        # branch on (doctor, exit code 4), not a raw traceback they'd treat as a
        # generic failure.
        raise CapabilityUnsupported(
            "list_windows", f"reading the window list failed: {exc}"
        ) from exc
    finally:
        display.close()
    return windows_from_raw(raws, os.getpid(), from_stacking=from_stacking)


# --- X11 shim: needs a live server + python-xlib + an EWMH WM ----------------


def _connect():  # pragma: no cover - needs a real X server
    """Open the X display, or raise CapabilityUnsupported explaining why not."""
    try:
        from Xlib import display as xdisplay
        from Xlib.error import DisplayError
    except ImportError as exc:
        raise CapabilityUnsupported(
            "list_windows", "python-xlib is not installed (needed for X11 window enumeration)"
        ) from exc
    if not os.environ.get("DISPLAY"):
        raise CapabilityUnsupported("list_windows", "no X display (DISPLAY is unset)")
    try:
        return xdisplay.Display()
    except (DisplayError, OSError) as exc:
        raise CapabilityUnsupported(
            "list_windows", f"cannot connect to the X server: {exc}"
        ) from exc


def _read_raw(display) -> tuple[list[RawWindow], bool]:  # pragma: no cover - needs a real X server
    """Read every managed window off the server as :class:`RawWindow` records.

    Returns the records plus whether the source was the stacking list. Each
    window is queried defensively: a window that closes mid-enumeration makes
    the server raise ``BadWindow``, which is skipped rather than aborting the
    whole list.
    """
    from Xlib import X
    from Xlib.error import XError

    root = display.screen().root
    ids = _window_ids(display, root, "_NET_CLIENT_LIST_STACKING")
    from_stacking = ids is not None
    if ids is None:
        ids = _window_ids(display, root, "_NET_CLIENT_LIST")
    if ids is None:
        raise CapabilityUnsupported("list_windows", "no EWMH-compatible window manager is running")

    utf8 = display.intern_atom("UTF8_STRING")
    net_name = display.intern_atom("_NET_WM_NAME")
    net_pid = display.intern_atom("_NET_WM_PID")
    net_type = display.intern_atom("_NET_WM_WINDOW_TYPE")

    raws: list[RawWindow] = []
    for wid in ids:
        try:
            win = display.create_resource_object("window", wid)
            attrs = win.get_attributes()
            geom = win.get_geometry()
            # Map the window's own (0, 0) into root coordinates so bounds are
            # absolute even under a reparenting WM that frames the client.
            coords = root.translate_coords(wid, 0, 0)
            raws.append(
                RawWindow(
                    window_id=int(wid),
                    title=_window_title(win, net_name, utf8),
                    wm_class=_window_class(win),
                    pid=_window_pid(win, net_pid),
                    bounds=Rect(
                        x=int(coords.x),
                        y=int(coords.y),
                        width=int(geom.width),
                        height=int(geom.height),
                    ),
                    viewable=attrs.map_state == X.IsViewable,
                    window_type=_window_type(display, win, net_type),
                )
            )
        except XError:
            continue
    return raws, from_stacking


def _window_ids(display, root, atom_name):  # pragma: no cover - needs a real X server
    """The window-id array from a root property, or None when it is absent."""
    from Xlib import X

    prop = root.get_full_property(display.intern_atom(atom_name), X.AnyPropertyType)
    if prop is None:
        return None
    return list(prop.value)


def _window_title(win, net_name, utf8):  # pragma: no cover - needs a real X server
    """The window's title: _NET_WM_NAME (UTF-8) first, then legacy WM_NAME."""
    prop = win.get_full_property(net_name, utf8)
    if prop is not None and prop.value:
        value = prop.value
        if isinstance(value, bytes):
            return value.decode("utf-8", "replace")
        return str(value)
    return win.get_wm_name() or ""


def _window_class(win):  # pragma: no cover - needs a real X server
    """The WM_CLASS *class* part (the stable app id), or None."""
    pair = win.get_wm_class()
    if not pair:
        return None
    # (instance, class); the class is the app-wide identity to match on.
    return pair[1] or pair[0] or None


def _window_pid(win, net_pid):  # pragma: no cover - needs a real X server
    """The owning process id from _NET_WM_PID, or None when unset."""
    from Xlib import X

    prop = win.get_full_property(net_pid, X.AnyPropertyType)
    if prop is None or not prop.value:
        return None
    return int(prop.value[0])


def _window_type(display, win, net_type):  # pragma: no cover - needs a real X server
    """The first _NET_WM_WINDOW_TYPE atom resolved to its name, or None."""
    from Xlib import X

    prop = win.get_full_property(net_type, X.AnyPropertyType)
    if prop is None or not prop.value:
        return None
    return display.get_atom_name(prop.value[0])
