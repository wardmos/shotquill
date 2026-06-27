# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the opt-in crop-adjust diagnostics (``ui._debug``).

``crop_log`` is a no-op unless ``SHOTQUILL_CROP_DEBUG`` was set at launch; when
on it appends geometry lines to ``<temp>/shotquill/crop-debug.log`` and must
never raise — diagnostics can't be allowed to break the app.
"""

from __future__ import annotations

from shotquill import paths
from shotquill.ui import _debug


def test_noop_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(_debug, "_ENABLED", False)
    monkeypatch.setattr(paths, "capture_tmp_dir", lambda: tmp_path)
    _debug.crop_log("should be ignored")
    assert list(tmp_path.iterdir()) == []


def test_appends_lines_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(_debug, "_ENABLED", True)
    monkeypatch.setattr(paths, "capture_tmp_dir", lambda: tmp_path)
    _debug.crop_log("entry one")
    _debug.crop_log("entry two")
    assert (tmp_path / "crop-debug.log").read_text(encoding="utf-8") == "entry one\nentry two\n"


def test_swallows_errors_when_enabled(monkeypatch):
    monkeypatch.setattr(_debug, "_ENABLED", True)

    def _boom():
        raise OSError("no temp dir")

    monkeypatch.setattr(paths, "capture_tmp_dir", _boom)
    # Must not propagate — diagnostics never break the app.
    _debug.crop_log("entry")
