# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""On-device text recognition (OCR)."""

from __future__ import annotations

import sys

from shotquill.ocr.base import TextRecognizer


def get_recognizer() -> TextRecognizer | None:
    """The platform OCR backend, or ``None`` when on-device OCR isn't available.

    macOS uses Apple Vision; Linux uses the Tesseract CLI when it is installed;
    Windows uses the WinRT OCR engine *when its optional dependency is installed*.
    When no backend is available the editor hides its OCR action rather than
    offering a button that can only fail. Construction is cheap — the heavy
    Vision/WinRT imports and the ``tesseract`` subprocess happen later, inside
    ``recognize()`` — but the Windows path runs a one-time import probe to decide
    whether the action is offered at all."""
    if sys.platform == "darwin":
        from shotquill.ocr.macos import VisionTextRecognizer

        return VisionTextRecognizer()
    if sys.platform.startswith("linux"):
        from shotquill.ocr.linux import TesseractTextRecognizer, tesseract_path

        if tesseract_path() is not None:
            return TesseractTextRecognizer()
    if sys.platform.startswith("win"):
        from shotquill.ocr import windows

        if windows.is_available():
            return windows.WindowsOcrRecognizer()
    return None
