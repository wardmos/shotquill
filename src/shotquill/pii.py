# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Best-effort detection of likely PII in recognized on-screen text (D15 layer 6).

This is the *weakest* privacy layer, and the only honest framing is "best-effort,
not a guarantee" (see decisions.md D15). It does **not** mask pixels: locating PII
on screen needs per-text bounding boxes, which the OCR backends do not yet surface
(decisions.md D11). What it does is *flag residual risk* — scan the recognized
text of a frame and report which kinds of sensitive value likely appear and how
many, so a reviewer or an export gate knows a frame probably carries a card number
or an email before it leaves the machine.

Findings carry the **kind and count only, never the matched value**. The manifest
is plain JSON on disk; writing the SSN we found into it would defeat the point.

Precision over recall: each detector is conservative (cards are Luhn-checked,
IPv4 octets range-checked, SSN/IBAN shape-checked, phone needs a separator), and
overlapping matches are resolved by priority — a 16-digit run is one card finding,
not also a phone. It will still miss things and occasionally false-positive; that
is the nature of layer 6, and why nothing here is presented as a guarantee.

Like :mod:`textassert`, this is pure: no Qt, no capture. It takes the lines a
recognizer already produced and reports what it found.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    """One kind of likely PII and how many times it appeared."""

    kind: str  # e.g. "email", "credit_card", "ssn", "ipv4", "iban", "phone"
    count: int


@dataclass(frozen=True)
class _Detector:
    kind: str
    pattern: re.Pattern[str]
    # Optional second-stage check on the matched text (Luhn, octet range, …);
    # lets the regex stay loose while the detector stays precise.
    validate: Callable[[str], bool] | None = None


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _luhn_ok(text: str) -> bool:
    """Luhn checksum — filters most non-card digit runs of card-ish length."""
    digits = [int(ch) for ch in _digits(text)]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, digit in enumerate(reversed(digits)):
        if i % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _ipv4_ok(text: str) -> bool:
    return all(part.isdigit() and int(part) <= 255 for part in text.split("."))


def _iban_ok(text: str) -> bool:
    # Country code + check digits + BBAN; total length is 15–34 across schemes.
    return 15 <= len(text) <= 34


# Detectors in priority order: when two matches overlap (a digit run that looks
# like both a card and a phone), the earlier detector wins and the later one is
# suppressed. Keep the most specific / highest-confidence kinds first.
_DETECTORS: tuple[_Detector, ...] = (
    _Detector(
        "email",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ),
    _Detector(
        "credit_card",
        re.compile(r"\b\d(?:[ -]?\d){12,18}\b"),
        validate=_luhn_ok,
    ),
    _Detector(
        "iban",
        re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
        validate=_iban_ok,
    ),
    _Detector(
        "ssn",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    ),
    _Detector(
        "ipv4",
        re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
        validate=_ipv4_ok,
    ),
    _Detector(
        # Conservative: requires a + prefix, parenthesised area code, or an
        # internal separator, so a bare 7-digit number is not flagged.
        "phone",
        re.compile(
            r"(?<![\w.])(?:\+\d{1,3}[ .-]?)?(?:\(\d{2,4}\)[ .-]?|\d{2,4}[ .-])"
            r"\d{2,4}[ .-]\d{2,4}(?![\w.])"
        ),
    ),
)


def find_spans(lines: str | Iterable[str]) -> list[tuple[str, int, int]]:
    """Locate likely-PII spans in the text as ``(kind, start, end)`` offsets.

    Accepts a single string or the line list a recognizer returns (joined with
    newlines, matching :mod:`textassert`). Spans are non-overlapping — a region
    claimed by a higher-priority detector is not re-reported by a lower one — and
    returned in document order. Offsets index the joined text, not any frame
    pixels (there are none here; see the module docstring).
    """
    text = lines if isinstance(lines, str) else "\n".join(lines)
    claimed: list[tuple[int, int]] = []
    spans: list[tuple[str, int, int]] = []
    for detector in _DETECTORS:
        for match in detector.pattern.finditer(text):
            if detector.validate is not None and not detector.validate(match.group()):
                continue
            start, end = match.start(), match.end()
            if any(start < c_end and c_start < end for c_start, c_end in claimed):
                continue  # overlaps a higher-priority match — skip
            claimed.append((start, end))
            spans.append((detector.kind, start, end))
    spans.sort(key=lambda span: span[1])
    return spans


def scan(lines: str | Iterable[str]) -> list[Finding]:
    """Report likely PII as per-kind counts — the value is never recorded.

    Best-effort, not a guarantee (D15 layer 6). Returns one :class:`Finding` per
    kind that appeared, ordered by descending count then kind for a stable,
    skimmable summary.
    """
    counts: dict[str, int] = {}
    for kind, _start, _end in find_spans(lines):
        counts[kind] = counts.get(kind, 0) + 1
    findings = [Finding(kind, count) for kind, count in counts.items()]
    findings.sort(key=lambda f: (-f.count, f.kind))
    return findings


def describe(findings: list[Finding]) -> str:
    """A one-line human summary of a scan (for the CLI's stderr)."""
    if not findings:
        return "pii scan: nothing flagged (best-effort, not a guarantee)"
    parts = ", ".join(f"{f.count} {f.kind}" for f in findings)
    return f"pii scan: likely {parts} (best-effort, not a guarantee)"
