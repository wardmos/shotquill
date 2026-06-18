# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Abstract interface for on-device OCR (platform implementations in siblings)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtGui import QImage


@dataclass(frozen=True)
class TextBox:
    """One recognized text span and its bounding box in the input image.

    Coordinates are **pixels in the** ``QImage`` **passed to**
    :meth:`TextRecognizer.recognize_boxes`, top-left origin — the very space
    :func:`shotquill.redact.fill_rects` masks, so a detected span can be redacted
    in place without a coordinate hop. ``width`` / ``height`` are non-negative and
    the box is clamped to the image, so it may sit flush against an edge but never
    extends past it.
    """

    text: str
    x: int
    y: int
    width: int
    height: int

    def as_rect(self) -> tuple[int, int, int, int]:
        """The box as the ``(x, y, w, h)`` pixel tuple ``fill_rects`` expects."""
        return (self.x, self.y, self.width, self.height)


class TextRecognizer(ABC):
    """Extracts text from an image, fully on-device (no network)."""

    #: Human-readable name of the underlying engine, surfaced by ``squill
    #: doctor`` so users know *which* on-device OCR answered (Apple Vision,
    #: Windows OCR, …). Subclasses override.
    backend_name: str = "on-device OCR"

    @abstractmethod
    def recognize_boxes(self, image: QImage) -> list[TextBox]:
        """Return recognized text spans with pixel bounding boxes.

        Ordered roughly top-to-bottom then left-to-right (the reading order the
        assertions rely on). This is the richer primitive: *locating* text on
        screen — to mask a detected card number, say — needs the box, not just the
        string. :meth:`recognize` is the text-only view, derived from this.
        """

    def recognize(self, image: QImage) -> list[str]:
        """Return recognized text lines, ordered roughly top-to-bottom.

        The text-only view of :meth:`recognize_boxes`, kept as the simple entry
        point for callers that only assert on or print the text.
        """
        return [box.text for box in self.recognize_boxes(image)]
