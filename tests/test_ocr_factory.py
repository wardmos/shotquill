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


def test_linux_returns_tesseract_when_installed(monkeypatch):
    monkeypatch.setattr(ocr.sys, "platform", "linux")
    from shotquill.ocr import linux

    monkeypatch.setattr(linux, "tesseract_path", lambda: "/usr/bin/tesseract")
    recognizer = ocr.get_recognizer()
    # Constructed without invoking tesseract (that happens inside recognize()).
    assert isinstance(recognizer, linux.TesseractTextRecognizer)


def test_linux_has_no_backend_without_tesseract(monkeypatch):
    monkeypatch.setattr(ocr.sys, "platform", "linux")
    from shotquill.ocr import linux

    monkeypatch.setattr(linux, "tesseract_path", lambda: None)
    assert ocr.get_recognizer() is None
