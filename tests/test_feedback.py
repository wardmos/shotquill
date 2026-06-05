# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for capture feedback (screen flash + optional shutter beep)."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from shotquill.ui.feedback import CaptureFeedback  # noqa: E402


def test_sound_beeps_only_when_requested(qapp, monkeypatch):
    calls = []
    monkeypatch.setattr(QApplication, "beep", staticmethod(lambda: calls.append(1)))

    feedback = CaptureFeedback()
    feedback.trigger(QRect(0, 0, 10, 10), flash=False, sound=False)
    assert calls == []

    feedback.trigger(QRect(0, 0, 10, 10), flash=False, sound=True)
    assert calls == [1]


def test_flash_creates_a_window_when_enabled(qapp, monkeypatch):
    # Don't actually beep during the flash assertion.
    monkeypatch.setattr(QApplication, "beep", staticmethod(lambda: None))
    feedback = CaptureFeedback()
    feedback.trigger(QRect(0, 0, 20, 20), flash=True, sound=False)
    assert len(feedback._flashes) == 1


def test_no_flash_window_when_disabled(qapp, monkeypatch):
    monkeypatch.setattr(QApplication, "beep", staticmethod(lambda: None))
    feedback = CaptureFeedback()
    feedback.trigger(QRect(0, 0, 20, 20), flash=False, sound=True)
    assert feedback._flashes == []


def test_flash_cleans_up_when_animation_finishes(qapp, qtbot, monkeypatch):
    monkeypatch.setattr(QApplication, "beep", staticmethod(lambda: None))
    feedback = CaptureFeedback()
    feedback.trigger(QRect(0, 0, 20, 20), flash=True, sound=False)
    qtbot.waitUntil(lambda: feedback._flashes == [], timeout=2000)


def test_overlapping_flashes_both_close(qapp, qtbot, monkeypatch):
    # Two captures within one fade: each animation must close its own window.
    # The old shared-slot bookkeeping closed the *new* flash from the old
    # animation's finished handler and leaked the old always-on-top window.
    monkeypatch.setattr(QApplication, "beep", staticmethod(lambda: None))
    feedback = CaptureFeedback()
    feedback.trigger(QRect(0, 0, 20, 20), flash=True, sound=False)
    feedback.trigger(QRect(0, 0, 20, 20), flash=True, sound=False)
    assert len(feedback._flashes) == 2
    qtbot.waitUntil(lambda: feedback._flashes == [], timeout=2000)
