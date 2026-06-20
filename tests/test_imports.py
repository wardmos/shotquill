# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Smoke test: every module imports cleanly.

Catches Qt import/symbol mistakes (wrong enums, moved classes) that the
pure-logic tests never exercise, and guards against a new module that fails to
import. The module list is **discovered**, not hand-maintained, so a newly added
file is covered automatically and the test can never silently fall behind.

Platform backends (``capture`` / ``hotkeys`` / ``ocr`` / ``autostart`` variants)
keep their OS-specific calls lazy — Xlib, WinRT, AppKit, the Win32/registry APIs
are imported inside functions — so every module imports on every platform; only
the calls fail off their OS. That is what makes a single cross-platform smoke
list correct. Skipped where PySide6 is unavailable.
"""

import importlib
import pkgutil

import pytest

pytest.importorskip("PySide6")

import shotquill


def _all_modules() -> list[str]:
    """Every importable module in the ``shotquill`` package, the package itself
    included — discovered by walking the package path so the set never drifts."""
    names = ["shotquill"]
    names += [info.name for info in pkgutil.walk_packages(shotquill.__path__, prefix="shotquill.")]
    return sorted(names)


@pytest.mark.parametrize("module", _all_modules())
def test_module_imports(module):
    importlib.import_module(module)
