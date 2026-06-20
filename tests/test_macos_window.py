# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the AppKit window glue's graceful fallback.

``set_resizable`` / ``raise_above_menubar`` re-level a Qt window's backing
NSWindow on macOS. The contract this pins is the *off-macOS / no-pyobjc* path:
when the ``objc`` bridge can't be imported both must return ``False`` (the
caller then keeps Qt's default window) rather than raising — so a capture never
crashes for lack of AppKit. Forcing the import to fail makes the test
deterministic on every platform, including the macOS CI leg.
"""

from __future__ import annotations

import sys

from shotquill.ui import macos_window


class _FakeWidget:
    def winId(self):  # pragma: no cover - never reached once the bridge is absent
        return 0


def test_set_resizable_returns_false_without_appkit(monkeypatch):
    # ``sys.modules["objc"] = None`` makes ``import objc`` raise ImportError.
    monkeypatch.setitem(sys.modules, "objc", None)
    assert macos_window.set_resizable(_FakeWidget(), True) is False
    assert macos_window.set_resizable(_FakeWidget(), False) is False


def test_raise_above_menubar_returns_false_without_appkit(monkeypatch):
    monkeypatch.setitem(sys.modules, "objc", None)
    assert macos_window.raise_above_menubar(_FakeWidget()) is False
