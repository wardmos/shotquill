# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the platform OCR factory (get_recognizer)."""

from __future__ import annotations

from shotquill import ocr


def test_macos_returns_vision_recognizer(monkeypatch):
    monkeypatch.setattr(ocr.sys, "platform", "darwin")
    recognizer = ocr.get_recognizer()
    # Constructed without importing Vision (that happens inside recognize()).
    from shotquill.ocr.macos import VisionTextRecognizer

    assert isinstance(recognizer, VisionTextRecognizer)


def test_linux_has_no_backend(monkeypatch):
    monkeypatch.setattr(ocr.sys, "platform", "linux")
    assert ocr.get_recognizer() is None
