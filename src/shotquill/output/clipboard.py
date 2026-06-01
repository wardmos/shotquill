# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Copy a captured image to the system clipboard via Qt."""

from __future__ import annotations

from shotquill.capture.base import CaptureResult


def copy_image(result: CaptureResult) -> None:
    """Place ``result`` on the clipboard. Requires a running QApplication."""
    from PySide6.QtGui import QGuiApplication, QImage

    image = QImage(
        result.pixels,
        result.width,
        result.height,
        QImage.Format.Format_RGBA8888,
    )
    # QImage does not own the Python bytes; copy() detaches it before the
    # buffer can be freed.
    QGuiApplication.clipboard().setImage(image.copy())
