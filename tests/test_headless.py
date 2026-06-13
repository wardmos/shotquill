# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the shared headless helpers (CLI / MCP seam)."""

import io

import pytest

from shotquill import headless


def test_read_image_bytes_returns_full_payload():
    data = b"\x89PNG" + b"\x00" * 100
    assert headless.read_image_bytes(io.BytesIO(data), label="x") == data


def test_read_image_bytes_allows_exactly_the_limit(monkeypatch):
    monkeypatch.setattr(headless, "MAX_IMAGE_BYTES", 16)
    payload = b"a" * 16
    assert headless.read_image_bytes(io.BytesIO(payload), label="x") == payload


def test_read_image_bytes_rejects_oversize(monkeypatch):
    # The cap stops an unbounded source (a huge file, or /dev/zero, which reports
    # no size) from being slurped whole into memory. It raises the typed,
    # exit-coded error so agents branch on it instead of a generic failure.
    monkeypatch.setattr(headless, "MAX_IMAGE_BYTES", 16)
    with pytest.raises(headless.ImageInputTooLarge, match="image limit") as exc:
        headless.read_image_bytes(io.BytesIO(b"a" * 17), label="huge")
    assert exc.value.exit_code == headless.EXIT_INVALID_INPUT
