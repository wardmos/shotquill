# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""macOS OCR via Apple's Vision framework (on-device, offline, free).

PyObjC + Vision are macOS-only, so they are imported lazily — this module still
imports cleanly on other platforms (e.g. for the CI smoke test).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from shotquill.ocr.base import TextBox, TextRecognizer

if TYPE_CHECKING:
    from PySide6.QtGui import QImage

_DEFAULT_LANGUAGES = ("zh-Hans", "en")


def pixel_box(
    nx: float, ny: float, nw: float, nh: float, image_w: int, image_h: int
) -> tuple[int, int, int, int]:
    """Vision's normalized ``boundingBox`` to a pixel ``(x, y, w, h)`` (pure).

    Vision reports boxes normalized to ``[0, 1]`` with a **bottom-left** origin;
    we want pixels with a **top-left** origin, the space the image (and
    ``redact.fill_rects``) uses. Flip the y axis and scale by the image size.
    Edges are floored/ceiled outward — like :func:`shotquill.redact.pixel_rect` —
    so a box fully covers its glyphs rather than leaving a one-pixel seam, then
    clamped to the image so it never reaches past the bounds.
    """
    left = math.floor(nx * image_w)
    right = math.ceil((nx + nw) * image_w)
    top = math.floor((1.0 - ny - nh) * image_h)
    bottom = math.ceil((1.0 - ny) * image_h)
    left = max(0, min(left, image_w))
    right = max(0, min(right, image_w))
    top = max(0, min(top, image_h))
    bottom = max(0, min(bottom, image_h))
    return (left, top, max(0, right - left), max(0, bottom - top))


class VisionTextRecognizer(TextRecognizer):
    backend_name = "Apple Vision"

    def recognize_boxes(self, image: QImage) -> list[TextBox]:
        import Quartz
        import Vision
        from Foundation import NSData
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice

        # QImage -> PNG bytes -> NSData -> CGImage.
        buffer_bytes = QByteArray()
        buffer = QBuffer(buffer_bytes)
        buffer.open(QIODevice.WriteOnly)
        image.save(buffer, "PNG")
        buffer.close()
        data = NSData.dataWithBytes_length_(bytes(buffer_bytes), buffer_bytes.size())

        source = Quartz.CGImageSourceCreateWithData(data, None)
        if source is None:
            return []
        cg_image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
        if cg_image is None:
            return []

        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(True)
        request.setRecognitionLanguages_(list(_DEFAULT_LANGUAGES))

        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)
        # PyObjC bridges ``performRequests:error:`` as either a bare BOOL or a
        # ``(BOOL, error)`` tuple depending on the metadata; tolerate both, and
        # degrade to "no text" on any Vision error rather than crashing (mirrors
        # the Windows backend's contract).
        try:
            outcome = handler.performRequests_error_([request], None)
            ok = outcome[0] if isinstance(outcome, tuple) else outcome
        except Exception:
            return []
        if not ok:
            return []

        # Vision returns observations in detection order, each with a normalized
        # boundingBox. Convert to pixel boxes (see pixel_box) and sort top-to-
        # bottom then left-to-right — without the x tiebreak, same-row text keeps
        # detection order, which can jumble a multi-column layout the assertions
        # read.
        width, height = image.width(), image.height()
        boxes: list[TextBox] = []
        for obs in request.results() or []:
            candidates = obs.topCandidates_(1)
            if not candidates or candidates.count() == 0:
                continue
            text = str(candidates.objectAtIndex_(0).string())
            bbox = obs.boundingBox()
            origin, size = bbox.origin, bbox.size
            x, y, w, h = pixel_box(origin.x, origin.y, size.width, size.height, width, height)
            boxes.append(TextBox(text=text, x=x, y=y, width=w, height=h))
        boxes.sort(key=lambda b: (b.y, b.x))
        return boxes
