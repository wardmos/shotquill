# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Abstract interface for on-device OCR (platform implementations in siblings)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtGui import QImage


class TextRecognizer(ABC):
    """Extracts text from an image, fully on-device (no network)."""

    #: Human-readable name of the underlying engine, surfaced by ``squill
    #: doctor`` so users know *which* on-device OCR answered (Apple Vision,
    #: Windows OCR, …). Subclasses override.
    backend_name: str = "on-device OCR"

    @abstractmethod
    def recognize(self, image: QImage) -> list[str]:
        """Return recognized text lines, ordered roughly top-to-bottom."""
