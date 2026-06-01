# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Copy images to the system clipboard via Qt."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shotquill.capture.base import CaptureResult
from shotquill.imaging import result_to_qimage

if TYPE_CHECKING:
    from PySide6.QtGui import QImage


def copy_qimage(image: QImage) -> None:
    """Place a QImage on the clipboard. Requires a running QApplication."""
    from PySide6.QtGui import QGuiApplication

    QGuiApplication.clipboard().setImage(image)


def copy_image(result: CaptureResult) -> None:
    """Place a raw capture on the clipboard."""
    copy_qimage(result_to_qimage(result))
