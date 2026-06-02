# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Shared pytest fixtures and headless test setup.

Two jobs:

1. *pynput fallback* — ``pynput`` needs an X server (or macOS) to import. CI has
   one (Xvfb on Linux, native on macOS), but a bare dev machine may not. When the
   real import fails we install a tiny stub so the pure-logic hotkey/app tests can
   still be collected and run; on CI the real library is used.
2. *isolated config* — a ``config`` fixture that points QSettings at a temp file so
   tests never touch (or depend on) the developer's real preferences.
"""

from __future__ import annotations

import sys
import types

import pytest

try:  # pragma: no cover - exercised implicitly by import success/failure
    import pynput  # noqa: F401
except Exception:  # pragma: no cover - only hit on a headless dev machine
    _pynput = types.ModuleType("pynput")
    _keyboard = types.ModuleType("pynput.keyboard")

    class _StubGlobalHotKeys:  # minimal stand-in; real behaviour is monkeypatched per-test
        def __init__(self, mapping: dict) -> None:
            self._mapping = mapping

        def start(self) -> None: ...

        def stop(self) -> None: ...

    _keyboard.GlobalHotKeys = _StubGlobalHotKeys
    _pynput.keyboard = _keyboard
    sys.modules["pynput"] = _pynput
    sys.modules["pynput.keyboard"] = _keyboard


@pytest.fixture
def config(tmp_path, qapp):
    """A ``Config`` backed by an isolated, temporary INI file (no real prefs touched)."""
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QSettings

    from shotquill.config import Config

    previous_format = QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))

    cfg = Config()
    cfg._settings.clear()
    try:
        yield cfg
    finally:
        cfg._settings.clear()
        QSettings.setDefaultFormat(previous_format)
