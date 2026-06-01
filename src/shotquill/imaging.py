# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Bridge raw capture pixels into Qt's QImage."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shotquill.capture.base import CaptureResult

if TYPE_CHECKING:
    from PySide6.QtGui import QImage


def result_to_qimage(result: CaptureResult) -> QImage:
    from PySide6.QtGui import QImage

    image = QImage(
        result.pixels,
        result.width,
        result.height,
        QImage.Format.Format_RGBA8888,
    )
    # Detach from the Python bytes before they can be garbage collected.
    return image.copy()
