# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""On-device text recognition (OCR)."""

from __future__ import annotations

import sys

from shotquill.ocr.base import TextRecognizer


def get_recognizer() -> TextRecognizer | None:
    """The platform OCR backend, or ``None`` when on-device OCR isn't available.

    macOS uses Apple Vision; there is no Linux backend yet, so the editor hides
    its OCR action rather than offering a button that can only fail. Construction
    is cheap — the heavy PyObjC/Vision imports happen inside ``recognize()``."""
    if sys.platform == "darwin":
        from shotquill.ocr.macos import VisionTextRecognizer

        return VisionTextRecognizer()
    return None
