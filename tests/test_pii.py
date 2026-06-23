# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the best-effort PII scanner (D15 layer 6).

Pure logic over recognized text — no Qt, no capture. Asserts on kinds and counts;
the scanner never returns the matched value, so neither do these tests.
"""

from __future__ import annotations

from shotquill import pii
from shotquill.ocr.base import TextBox


def _kinds(text):
    return {f.kind for f in pii.scan(text)}


# --- per-detector -----------------------------------------------------------


def test_email_detected():
    assert _kinds("contact ada@example.com please") == {"email"}


def test_credit_card_luhn_valid_detected():
    assert _kinds("card 4111111111111111 on file") == {"credit_card"}


def test_credit_card_invalid_luhn_not_flagged():
    # A 16-digit run that fails the Luhn check is not a card (and too long with no
    # separators to be a phone), so nothing is flagged.
    assert _kinds("ref 4111111111111112") == set()


def test_credit_card_with_high_digits_passes_luhn():
    # 4012888888881881 is Luhn-valid; its 8s double past 9, exercising the
    # subtract-9 branch of the checksum (the all-1s test number never does).
    assert _kinds("card 4012 8888 8888 1881") == {"credit_card"}


def test_luhn_only_applies_to_card_length_runs():
    # The checksum guards on length first: a run shorter than 13 or longer than
    # 19 digits is rejected outright, never reaching the card detector.
    assert pii._luhn_ok("4111") is False
    assert pii._luhn_ok("4" * 20) is False


def test_ssn_detected():
    assert _kinds("ssn 078-05-1120 redacted?") == {"ssn"}


def test_ipv4_valid_detected():
    assert _kinds("host 192.168.1.1 up") == {"ipv4"}


def test_ipv4_out_of_range_not_flagged():
    assert _kinds("build 999.1.1.1 tag") == set()


def test_iban_detected():
    assert _kinds("IBAN DE89370400440532013000 ok") == {"iban"}


def test_phone_needs_a_separator():
    assert _kinds("call (415) 555-1234 now") == {"phone"}
    # A bare 7-digit number is not enough to call a phone.
    assert "phone" not in _kinds("order 1234567 shipped")


def test_plain_text_flags_nothing():
    assert pii.scan(["Welcome, Ada", "Dashboard", "Total items: 3"]) == []


# --- aggregation & overlap --------------------------------------------------


def test_scan_counts_and_orders_by_count_then_kind():
    findings = pii.scan("a@x.com b@y.com 192.168.0.1")
    assert findings == [pii.Finding("email", 2), pii.Finding("ipv4", 1)]


def test_overlapping_match_resolved_by_priority():
    # A separator-grouped 16-digit card also looks like a phone; the higher-
    # priority card detector claims the span and phone is suppressed (one finding).
    assert pii.scan("pay 4111 1111 1111 1111") == [pii.Finding("credit_card", 1)]


def test_accepts_line_list_like_a_recognizer():
    # scan() takes the list[str] a recognizer returns, joined with newlines.
    assert _kinds(["name: Ada", "email: ada@example.com"]) == {"email"}


# --- redaction_rects: spans -> pixel rects to mask ---------------------------


def test_redaction_rects_masks_the_box_carrying_pii():
    boxes = [
        TextBox("Welcome, Ada", 0, 0, 120, 10),
        TextBox("card 4111111111111111", 0, 20, 200, 10),
    ]
    # Only the second box carries PII; returned as (x0,y0,x1,y1) corners.
    assert pii.redaction_rects(boxes) == [(0, 20, 200, 30)]


def test_redaction_rects_empty_when_no_pii():
    assert pii.redaction_rects([TextBox("Dashboard", 0, 0, 80, 10)]) == []


def test_redaction_rects_handles_no_boxes():
    assert pii.redaction_rects([]) == []


def test_redaction_rects_masks_each_pii_box_in_reading_order():
    boxes = [
        TextBox("ada@example.com", 0, 0, 100, 10),
        TextBox("just a heading", 0, 20, 100, 10),
        TextBox("ssn 078-05-1120", 0, 40, 100, 10),
    ]
    # Two separate findings on lines 0 and 2; the middle box is left alone.
    assert pii.redaction_rects(boxes) == [(0, 0, 100, 10), (0, 40, 100, 50)]


# --- describe ---------------------------------------------------------------


def test_describe_nothing():
    assert "nothing flagged" in pii.describe([])


def test_describe_summarizes_without_values():
    line = pii.describe([pii.Finding("credit_card", 1), pii.Finding("email", 2)])
    assert "1 credit_card" in line and "2 email" in line
    assert "not a guarantee" in line
