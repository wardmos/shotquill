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
from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    """One assertion and whether it held against the recognized text."""

    kind: str  # "contains" | "matches"
    pattern: str
    passed: bool


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
    """
    text = "\n".join(lines)
    haystack = text.casefold() if ignore_case else text
    flags = re.IGNORECASE if ignore_case else 0

    checks: list[Check] = []
    for needle in contains:
        probe = needle.casefold() if ignore_case else needle
        checks.append(Check("contains", needle, probe in haystack))
    for pattern in matches:
        try:
            found = re.search(pattern, text, flags) is not None
        except re.error as exc:
            raise ValueError(f"invalid --matches regex {pattern!r}: {exc}") from None
        checks.append(Check("matches", pattern, found))
    return checks


def all_passed(checks: list[Check]) -> bool:
    """True when every check held (vacuously true for no checks)."""
    return all(check.passed for check in checks)


def describe(check: Check) -> str:
    """A one-line human summary of a check's outcome (for the CLI's stderr)."""
    verb = "contains" if check.kind == "contains" else "matches"
    status = "ok" if check.passed else "FAIL"
    return f"{status}: text {verb} {check.pattern!r}"
