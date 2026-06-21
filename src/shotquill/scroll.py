# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Synthesize mouse-wheel scrolling for the automatic long-screenshot path.

Phase 1's long screenshot has the human scroll; this drives the wheel itself so a
single ``squill capture --scrolling --auto`` walks down a page unattended. It
leans on ``pynput`` — already a dependency for global hotkeys — whose mouse
controller wraps the platform's native event synthesis (Quartz on macOS, SendInput
on Windows, XTest on X11), so one implementation covers all three rather than a
hand-rolled backend each.

Wayland is the deliberate gap: the compositor refuses synthetic input out of band
(the same reason global hotkeys are blocked there), so :func:`get_scroller` raises
:class:`shotquill.headless.CapabilityUnsupported` and the user falls back to plain
``--scrolling`` and their own scroll wheel.

``pynput.mouse`` is imported lazily inside the backend, never at module load, so
this module stays importable on a headless box without a mouse backend (the test
suite injects a fake scroller and never touches pynput).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Scroller(ABC):
    """Posts mouse-wheel events to scroll whatever is under the pointer."""

    @abstractmethod
    def scroll(self, clicks: int, *, at: tuple[int, int] | None = None) -> None:
        """Turn the wheel ``clicks`` notches; positive is up, negative is down.

        ``at`` (logical screen coordinates) moves the pointer there first, so the
        wheel events land on the region being captured rather than wherever the
        pointer happened to rest."""


class PynputScroller(Scroller):
    """The real backend: pynput's cross-platform mouse controller."""

    def __init__(self) -> None:
        from pynput.mouse import Controller

        self._mouse = Controller()

    def scroll(self, clicks: int, *, at: tuple[int, int] | None = None) -> None:
        if at is not None:
            self._mouse.position = at
        self._mouse.scroll(0, clicks)


def get_scroller() -> Scroller:
    """Return the platform scroller, or refuse on Wayland.

    Wayland blocks out-of-band synthetic input, so auto-scroll is unavailable
    there by design — distinct from a failure, so the caller (and an agent) can
    stop retrying and fall back to manual ``--scrolling``."""
    from shotquill.headless import CapabilityUnsupported, _is_wayland_session

    if _is_wayland_session():
        raise CapabilityUnsupported(
            "auto-scroll",
            "Wayland blocks synthetic input; scroll the page yourself with plain --scrolling",
        )
    return PynputScroller()
