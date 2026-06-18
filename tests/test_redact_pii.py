# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Content-level PII redaction: headless.redact_pii masks the matched pixels.

The detection (which boxes carry PII) is unit-tested in test_pii.py; here we
check the glue actually edits the bytes — a recognized PII box becomes opaque
fill, everything else is left intact. A fake recognizer stands in for a real OCR
engine so this runs headless.
"""

from __future__ import annotations

import pytest

from shotquill import headless
from shotquill.capture.base import CaptureResult
from shotquill.ocr.base import TextBox

pytest.importorskip("PySide6")

_BG = (200, 200, 200, 255)
_FILL = (0, 0, 0, 255)


def _solid(width, height):
    return CaptureResult(
        width=width, height=height, scale=1.0, pixels=bytes(list(_BG) * width * height)
    )


def _px(result, x, y):
    i = (y * result.width + x) * 4
    return tuple(result.pixels[i : i + 4])


class _FakeRecognizer:
    """Returns fixed boxes regardless of pixels (the detection is tested elsewhere)."""

    def __init__(self, boxes):
        self._boxes = boxes

    def recognize_boxes(self, image):
        return self._boxes


def test_redact_pii_fills_the_pii_box_and_leaves_the_rest():
    result = _solid(10, 10)
    # One PII box covering the rectangle x:2..5, y:3..6.
    rec = _FakeRecognizer([TextBox("ada@example.com", 2, 3, 3, 3)])
    out = headless.redact_pii(result, rec)

    assert _px(out, 3, 4) == _FILL  # inside the box → masked
    assert _px(out, 0, 0) == _BG  # outside → untouched
    assert _px(out, 6, 6) == _BG


def test_redact_pii_no_findings_returns_input_unchanged():
    result = _solid(8, 8)
    rec = _FakeRecognizer([TextBox("just a heading", 0, 0, 8, 2)])
    out = headless.redact_pii(result, rec)
    assert out.pixels == result.pixels


def test_redact_pii_no_text_at_all_is_a_noop():
    result = _solid(4, 4)
    out = headless.redact_pii(result, _FakeRecognizer([]))
    assert out.pixels == result.pixels
