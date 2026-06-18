# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Pure-logic tests for OCR bounding boxes (D11).

The box math and parsing are split out of the platform engines so they run with
no Qt, no Vision/WinRT, no tesseract — exactly the seam the backends keep. Each
backend's engine call stays ``# pragma: no cover``; the decisions worth testing
(coordinate flip, TSV grouping, word-rect union, the text-only derivation) live
in the pure helpers exercised here.
"""

from __future__ import annotations

from types import SimpleNamespace

from shotquill.ocr.base import TextBox, TextRecognizer
from shotquill.ocr.linux import boxes_from_tsv
from shotquill.ocr.macos import pixel_box
from shotquill.ocr.windows import boxes_from_result, lines_from_result

# --- base: recognize() is the text-only view of recognize_boxes() -------------


class _FakeRecognizer(TextRecognizer):
    def __init__(self, boxes):
        self._boxes = boxes

    def recognize_boxes(self, image):  # image is ignored by the fake
        return self._boxes


def test_recognize_derives_text_from_boxes():
    rec = _FakeRecognizer([TextBox("a", 0, 0, 1, 1), TextBox("b", 2, 2, 3, 3)])
    assert rec.recognize(image=None) == ["a", "b"]


def test_textbox_as_rect_is_fill_rects_tuple():
    assert TextBox("x", 4, 5, 6, 7).as_rect() == (4, 5, 6, 7)


# --- Tesseract TSV parsing ----------------------------------------------------

_HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
)


def _doc(*rows):
    return "\n".join([_HEADER, *rows]) + "\n"


def test_tsv_groups_words_into_lines_with_union_box():
    boxes = boxes_from_tsv(
        _doc(
            "5\t1\t1\t1\t1\t1\t10\t10\t40\t20\t96\thello",
            "5\t1\t1\t1\t1\t2\t60\t12\t50\t18\t95\tworld",
        )
    )
    assert len(boxes) == 1
    assert boxes[0].text == "hello world"
    # Union: x 10..110, y 10..30.
    assert boxes[0].as_rect() == (10, 10, 100, 20)


def test_tsv_drops_negative_confidence_and_empty_text():
    boxes = boxes_from_tsv(
        _doc(
            "4\t1\t1\t1\t1\t0\t0\t0\t100\t20\t-1\t",  # line scaffold row, no text
            "5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t-1\tghost",  # conf -1 → not a real word
            "5\t1\t1\t1\t1\t2\t0\t0\t10\t10\t90\t   ",  # whitespace only
            "5\t1\t1\t1\t1\t3\t5\t5\t10\t10\t88\tkeep",
        )
    )
    assert [b.text for b in boxes] == ["keep"]


def test_tsv_orders_lines_top_to_bottom_then_left():
    boxes = boxes_from_tsv(
        _doc(
            "5\t1\t1\t1\t2\t1\t10\t90\t20\t10\t90\tlower",
            "5\t1\t1\t1\t1\t1\t10\t10\t20\t10\t90\tupper",
        )
    )
    assert [b.text for b in boxes] == ["upper", "lower"]


def test_tsv_empty_and_unrecognized_header_yield_no_boxes():
    assert boxes_from_tsv("") == []
    assert boxes_from_tsv("unexpected\tcolumns\n1\t2\n") == []


# --- macOS Vision coordinate flip ---------------------------------------------


def test_pixel_box_flips_y_axis():
    # Vision bottom-left normalized box at the very bottom-left corner, 50%×10%
    # of a 200×100 image → top-left pixel box near the image's bottom edge.
    assert pixel_box(0.0, 0.0, 0.5, 0.1, 200, 100) == (0, 90, 100, 10)


def test_pixel_box_top_left_corner():
    # A box flush against the top in Vision space (origin.y near 1 - height).
    assert pixel_box(0.0, 0.9, 0.25, 0.1, 200, 100) == (0, 0, 50, 10)


def test_pixel_box_clamps_to_image_bounds():
    # Rounding outward can't push the box past the image.
    x, y, w, h = pixel_box(0.0, 0.0, 1.0, 1.0, 200, 100)
    assert (x, y) == (0, 0)
    assert x + w <= 200 and y + h <= 100


# --- Windows OcrResult word-rect union ----------------------------------------


def _rect(x, y, w, h):
    return SimpleNamespace(x=x, y=y, width=w, height=h)


def _line(text, *rects):
    return SimpleNamespace(text=text, words=[SimpleNamespace(bounding_rect=r) for r in rects])


def test_windows_line_box_is_union_of_word_rects():
    result = SimpleNamespace(lines=[_line("hi there", _rect(10, 10, 20, 8), _rect(40, 12, 30, 6))])
    boxes = boxes_from_result(result)
    assert len(boxes) == 1
    assert boxes[0].text == "hi there"
    assert boxes[0].as_rect() == (10, 10, 60, 8)  # x 10..70, y 10..18


def test_windows_line_without_words_keeps_text_with_zero_box():
    result = SimpleNamespace(lines=[_line("orphan")])
    assert boxes_from_result(result) == [TextBox("orphan", 0, 0, 0, 0)]


def test_windows_none_line_list_yields_empty():
    assert boxes_from_result(SimpleNamespace(lines=None)) == []


def test_windows_lines_from_result_is_text_only_view():
    result = SimpleNamespace(lines=[_line("a", _rect(0, 0, 1, 1)), _line("  ")])
    assert lines_from_result(result) == ["a"]
