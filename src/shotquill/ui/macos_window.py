# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Raise a Qt overlay window above the macOS menu bar, on every Space.

A plain Qt ``WindowStaysOnTopHint`` top-level still sits *below* the system
menu bar and only composites on the display it is assigned to (with macOS's
default per-display Spaces). The smart-capture overlay must cover the whole of
each display — menu bar included — so the per-screen overlays push their native
``NSWindow`` to a level above the menu bar and mark them to join every Space.

This is the only AppKit-specific glue; it is a no-op (and silently degrades to
the plain stay-on-top behaviour) if pyobjc isn't importable. macOS only.
"""

from __future__ import annotations

# NSWindow level used by screen savers: above the menu bar (24) and the Dock,
# so the overlay paints the frozen menu-bar pixels over the live one.
_NS_SCREENSAVER_LEVEL = 1000
# CollectionBehavior bits: appear on every Space (so a window whose frame spans
# two displays shows on both), don't slide between Spaces, and don't get pulled
# into another window's native full-screen Space.
_NS_CAN_JOIN_ALL_SPACES = 1 << 0
_NS_STATIONARY = 1 << 4
_NS_FULLSCREEN_AUXILIARY = 1 << 8


def raise_above_menubar(widget) -> bool:  # pragma: no cover - macOS/AppKit only
    """Push ``widget``'s NSWindow above the menu bar and onto every Space.

    Returns True on success, False if the AppKit bridge is unavailable (caller
    then keeps the plain Qt stay-on-top window). Must be called after the window
    is created (``show()`` / a valid ``winId()``).
    """
    try:
        import objc  # noqa: F401 - import proves the bridge is present

        # winId() returns the NSView* for the Qt window on the cocoa platform;
        # its .window() is the backing NSWindow we want to re-level.
        nsview = objc.objc_object(c_void_p=int(widget.winId()))
        nswindow = nsview.window()
        if nswindow is None:
            return False
        nswindow.setLevel_(_NS_SCREENSAVER_LEVEL)
        nswindow.setCollectionBehavior_(
            _NS_CAN_JOIN_ALL_SPACES | _NS_STATIONARY | _NS_FULLSCREEN_AUXILIARY
        )
        return True
    except Exception:
        # No pyobjc, not really cocoa, or the private layout changed: fall back
        # to the plain stay-on-top window rather than crash the capture.
        return False
