# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Assert on recognized on-screen text — the step from *capture* to *assert*.

A screenshot answers "what is on screen?"; a test needs "is the right thing on
screen?". AI-generated apps have no golden image to pixel-diff against — every
build is new — so the useful question is semantic: did the *Login* button render,
is the title right, is there an error dialog? That is exactly what OCR + a text
assertion answers, and what a bare screenshot cannot.

This module is the pure assertion core, shared by the CLI (`squill ocr
--contains/--matches`, which turns the result into an exit code) and the MCP ocr
tool (which returns it as structured data). It holds no Qt and does no capture:
it takes the lines a recognizer already produced and reports which checks held.

Substring (`contains`) and regex (`matches`) are evaluated against the OCR text
as one newline-joined block, so a phrase that the recognizer split across lines
still matches. Multiple checks are ANDed — every one must hold — which is what a
caller asserting "both the heading and the button rendered" expects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shotquill.ocr.base import TextBox


@dataclass(frozen=True)
class Check:
    """One assertion and whether it held against the recognized text.

    ``boxes`` holds the recognized text boxes that the match landed in — empty
    when the check failed, when no location info was available (the text-only
    :func:`evaluate`), or when the matched span carried no box. It answers *where*
    on screen the asserted text is, not just *whether* it is there.
    """

    kind: str  # "contains" | "matches"
    pattern: str
    passed: bool
    boxes: tuple[TextBox, ...] = field(default=())


def _spans(
    text: str,
    *,
    contains: tuple[str, ...],
    matches: tuple[str, ...],
    ignore_case: bool,
):
    """Yield ``(kind, pattern, span)`` for each check; ``span`` is ``None`` if unmatched.

    Both ``contains`` (literal) and ``matches`` (regex) are resolved with
    :func:`re.search` over the *original* text, so the returned span indexes the
    real string — case-folding can change length, so searching a folded copy would
    misplace it. Raises :class:`ValueError` on an invalid regex.
    """
    flags = re.IGNORECASE if ignore_case else 0
    for needle in contains:
        match = re.search(re.escape(needle), text, flags)
        yield "contains", needle, (match.span() if match else None)
    for pattern in matches:
        try:
            match = re.search(pattern, text, flags)
        except re.error as exc:
            raise ValueError(f"invalid --matches regex {pattern!r}: {exc}") from None
        yield "matches", pattern, (match.span() if match else None)


def evaluate(
    lines: list[str],
    *,
    contains: tuple[str, ...] = (),
    matches: tuple[str, ...] = (),
    ignore_case: bool = False,
) -> list[Check]:
    """Run each ``contains`` / ``matches`` check over the recognized lines.

    Returns one :class:`Check` per pattern, in the order given (all ``contains``
    then all ``matches``). Joins the lines with newlines first, so a match may
    span the recognizer's line breaks. Raises :class:`ValueError` on an invalid
    regex, so the caller can report it as a usage error rather than a failed
    assertion (a broken pattern is the caller's bug, not the screen's state).

    This is the text-only path: the returned checks carry no boxes. Use
    :func:`evaluate_boxes` when *where* the match landed matters.
    """
    text = "\n".join(lines)
    return [
        Check(kind, pattern, span is not None)
        for kind, pattern, span in _spans(
            text, contains=contains, matches=matches, ignore_case=ignore_case
        )
    ]


def evaluate_boxes(
    boxes: list[TextBox],
    *,
    contains: tuple[str, ...] = (),
    matches: tuple[str, ...] = (),
    ignore_case: bool = False,
) -> list[Check]:
    """Like :func:`evaluate`, but locate each match in the recognized boxes.

    The boxes' text is joined with newlines exactly as :func:`evaluate` joins
    lines, so the verdicts are identical; additionally each passing check's
    ``boxes`` lists the recognized boxes its matched span overlaps — the pixels to
    highlight, or to mask. Same coordinate space as :class:`TextBox` (image
    pixels), so a located box can go straight to ``redact.fill_rects``.
    """
    text = "\n".join(box.text for box in boxes)
    spans = _line_offsets(boxes)
    checks: list[Check] = []
    for kind, pattern, span in _spans(
        text, contains=contains, matches=matches, ignore_case=ignore_case
    ):
        located = _boxes_in_span(boxes, spans, span) if span is not None else ()
        checks.append(Check(kind, pattern, span is not None, located))
    return checks


def _line_offsets(boxes: list[TextBox]) -> list[tuple[int, int]]:
    """The ``[start, end)`` char range each box occupies in the newline-joined text."""
    offsets: list[tuple[int, int]] = []
    pos = 0
    for box in boxes:
        end = pos + len(box.text)
        offsets.append((pos, end))
        pos = end + 1  # +1 for the "\n" that join() inserts between boxes
    return offsets


def _boxes_in_span(
    boxes: list[TextBox], offsets: list[tuple[int, int]], span: tuple[int, int]
) -> tuple[TextBox, ...]:
    """The boxes whose text range overlaps ``span`` (a zero-width match touches none)."""
    start, end = span
    return tuple(
        box for box, (bs, be) in zip(boxes, offsets, strict=True) if bs < end and start < be
    )


def union_rect(boxes: tuple[TextBox, ...]) -> tuple[int, int, int, int] | None:
    """The bounding box that covers every box in ``boxes`` as ``(x, y, w, h)``.

    ``None`` for an empty input. Useful for reporting "the match is *here*" as a
    single rectangle (e.g. the CLI's stderr) or masking it in one fill.
    """
    if not boxes:
        return None
    x0 = min(b.x for b in boxes)
    y0 = min(b.y for b in boxes)
    x1 = max(b.x + b.width for b in boxes)
    y1 = max(b.y + b.height for b in boxes)
    return (x0, y0, x1 - x0, y1 - y0)


def all_passed(checks: list[Check]) -> bool:
    """True when every check held (vacuously true for no checks)."""
    return all(check.passed for check in checks)


def describe(check: Check) -> str:
    """A one-line human summary of a check's outcome (for the CLI's stderr)."""
    verb = "contains" if check.kind == "contains" else "matches"
    status = "ok" if check.passed else "FAIL"
    summary = f"{status}: text {verb} {check.pattern!r}"
    rect = union_rect(check.boxes)
    if rect is not None:
        summary += " at {},{},{},{}".format(*rect)
    return summary
