# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""macOS OCR via Apple's Vision framework (on-device, offline, free).

PyObjC + Vision are macOS-only, so they are imported lazily — this module still
imports cleanly on other platforms (e.g. for the CI smoke test).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shotquill.ocr.base import TextRecognizer

if TYPE_CHECKING:
    from PySide6.QtGui import QImage

_DEFAULT_LANGUAGES = ("zh-Hans", "en")


class VisionTextRecognizer(TextRecognizer):
    backend_name = "Apple Vision"

    def recognize(self, image: QImage) -> list[str]:
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

        # Vision returns observations in detection order; sort top-to-bottom,
        # then left-to-right (boundingBox origin is bottom-left, so larger y is
        # higher up). Without the x tiebreak, same-row text keeps detection
        # order, which can jumble a multi-column layout the assertions read.
        observations = list(request.results() or [])
        observations.sort(key=lambda obs: (-obs.boundingBox().origin.y, obs.boundingBox().origin.x))

        lines = []
        for obs in observations:
            candidates = obs.topCandidates_(1)
            if candidates and candidates.count() > 0:
                lines.append(str(candidates.objectAtIndex_(0).string()))
        return lines
