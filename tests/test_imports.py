# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Smoke test: every module imports cleanly.

Catches Qt import/symbol mistakes (wrong enums, moved classes) that the
pure-logic tests never exercise. Skipped where PySide6 is unavailable.
"""

import importlib

import pytest

pytest.importorskip("PySide6")

_MODULES = [
    "shotquill",
    "shotquill.app",
    "shotquill.config",
    "shotquill.i18n",
    "shotquill.imaging",
    "shotquill.capture.base",
    "shotquill.capture.macos",
    "shotquill.hotkeys.base",
    "shotquill.hotkeys.macos",
    "shotquill.hotkeys.combo",
    "shotquill.output.saver",
    "shotquill.output.clipboard",
    "shotquill.ui.tools",
    "shotquill.ui.geometry",
    "shotquill.ui.canvas",
    "shotquill.ui.editor",
    "shotquill.ui.overlay",
    "shotquill.ui.toolbar",
    "shotquill.ui.settings",
    "shotquill.ui.items.geometry",
    "shotquill.ui.items.arrow",
    "shotquill.ui.items.mosaic",
    "shotquill.ocr.base",
    "shotquill.ocr.macos",
]


@pytest.mark.parametrize("module", _MODULES)
def test_module_imports(module):
    importlib.import_module(module)
