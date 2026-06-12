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

Coordinates are X11 pixels with a top-left origin, which equal Qt's logical
virtual-desktop coordinates on a standard-DPI display. On a fractionally scaled
X11 desktop the two can diverge; window picking still works, but redaction
geometry is best-effort there. Wayland is handled separately — the compositor
forbids an app from enumerating other apps' windows at all (see ``wayland.py``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

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


def list_windows() -> list[WindowInfo]:
    """On-screen application windows, front-most first (the X11 backend path).

    Raises :class:`CapabilityUnsupported` when the platform can't answer —
    ``python-xlib`` not installed, no reachable X server, or no EWMH-compliant
    window manager running — so the caller emits a typed "unsupported" signal
    instead of an empty list a user would misread as "nothing on screen".
    """
    display = _connect()
    try:
        raws, from_stacking = _read_raw(display)
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
