# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Synthesize mouse-wheel scrolling for the automatic long-screenshot path.

This drives the wheel so a single ``squill capture --scrolling --auto`` (or MCP
``capture`` with ``scrolling``) walks down a page unattended. It leans on
``pynput`` — already a dependency for global hotkeys — whose mouse controller wraps
the platform's native event synthesis (Quartz on macOS, SendInput on Windows, XTest
on X11), so one implementation covers all three rather than a hand-rolled backend
each.

Wayland is the deliberate gap: its Screenshot portal provides isolated stills and
the compositor refuses synthetic input. Until a ScreenCast/PipeWire stream backend
exists, :func:`get_scroller` raises
:class:`shotquill.headless.CapabilityUnsupported`.

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

    def close(self) -> None:
        """Release input state after capture (no-op for stateless backends)."""
        return None


class PynputScroller(Scroller):
    """The real backend: pynput's cross-platform mouse controller."""

    def __init__(self) -> None:
        from pynput.mouse import Controller

        self._mouse = Controller()
        self._initial_position = tuple(self._mouse.position)

    def scroll(self, clicks: int, *, at: tuple[int, int] | None = None) -> None:
        if at is not None:
            self._mouse.position = at
        self._mouse.scroll(0, clicks)

    def close(self) -> None:
        initial = self._initial_position
        self._initial_position = None
        if initial is not None:
            self._mouse.position = initial


def get_scroller() -> Scroller:
    """Return the platform scroller, or refuse on Wayland.

    Wayland blocks both the repeated out-of-band capture and synthetic input this
    workflow needs. The typed refusal lets callers stop retrying and explain that
    a ScreenCast/PipeWire backend is required."""
    from shotquill.headless import CapabilityUnsupported, _is_wayland_session

    if _is_wayland_session():
        raise CapabilityUnsupported(
            "auto-scroll",
            "Wayland long screenshots need continuous ScreenCast/PipeWire capture "
            "and synthetic input support",
        )
    return PynputScroller()
