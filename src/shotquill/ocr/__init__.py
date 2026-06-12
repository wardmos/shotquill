# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""On-device text recognition (OCR)."""

from __future__ import annotations

import sys

from shotquill.ocr.base import TextRecognizer


def get_recognizer() -> TextRecognizer | None:
    """The platform OCR backend, or ``None`` when on-device OCR isn't available.

    macOS uses Apple Vision; Linux uses the Tesseract CLI when it is installed.
    When no backend is available the editor hides its OCR action rather than
    offering a button that can only fail. Construction is cheap — the heavy
    PyObjC/Vision imports and the ``tesseract`` subprocess happen later, inside
    ``recognize()``."""
    if sys.platform == "darwin":
        from shotquill.ocr.macos import VisionTextRecognizer

        return VisionTextRecognizer()
    if sys.platform.startswith("linux"):
        from shotquill.ocr.linux import TesseractTextRecognizer, tesseract_path

        if tesseract_path() is not None:
            return TesseractTextRecognizer()
    return None
