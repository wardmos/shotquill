# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Copy images to the system clipboard via Qt."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shotquill.capture.base import CaptureResult
from shotquill.imaging import result_to_qimage

if TYPE_CHECKING:
    from PySide6.QtGui import QImage


def _clipboard():
    """The system clipboard, or a clear error when there's no QApplication.

    ``QGuiApplication.clipboard()`` returns ``None`` without a running app, which
    would otherwise surface as an opaque ``AttributeError`` on the next call."""
    from PySide6.QtGui import QGuiApplication

    clipboard = QGuiApplication.clipboard()
    if clipboard is None:
        raise RuntimeError("clipboard requires a running QApplication")
    return clipboard


def copy_qimage(image: QImage) -> None:
    """Place a QImage on the clipboard. Requires a running QApplication."""
    _clipboard().setImage(image)


def copy_image(result: CaptureResult) -> None:
    """Place a raw capture on the clipboard."""
    copy_qimage(result_to_qimage(result))


def copy_text(text: str) -> None:
    """Place plain text on the clipboard (used for OCR results)."""
    _clipboard().setText(text)
