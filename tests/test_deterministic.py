# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Deterministic-encoding tests: the same pixels must yield the same bytes.

The pure chunk maths needs no Qt; the image-level checks build a QImage under
the offscreen platform (conftest forces it) and round-trip through
``headless.encode_qimage``.
"""

from __future__ import annotations

import zlib

import pytest

from shotquill import deterministic as det

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(chunk_type: bytes, data: bytes = b"") -> bytes:
    """Assemble one PNG chunk (length + type + data + CRC)."""
    body = chunk_type + data
    return len(data).to_bytes(4, "big") + body + zlib.crc32(body).to_bytes(4, "big")


def _chunk_types(png: bytes) -> list[bytes]:
    out: list[bytes] = []
    i = len(_PNG_SIGNATURE)
    while i + 8 <= len(png):
        length = int.from_bytes(png[i : i + 4], "big")
        out.append(png[i + 4 : i + 8])
        i += 12 + length
    return out


# --- strip_volatile_png_chunks: pure bytes ----------------------------------


def test_strip_drops_timestamp_and_text_keeps_the_rest():
    png = (
        _PNG_SIGNATURE
        + _chunk(b"IHDR", b"\x00" * 13)
        + _chunk(b"tIME", b"\x07\xea\x06\x0d\x0a\x1e\x00")  # a wall-clock stamp
        + _chunk(b"pHYs", b"\x00\x00\x0b\x13\x00\x00\x0b\x13\x01")
        + _chunk(b"tEXt", b"Software\x00ShotQuill")
        + _chunk(b"IDAT", b"\x78\x9c\x00")
        + _chunk(b"IEND")
    )
    out = det.strip_volatile_png_chunks(png)
    assert _chunk_types(out) == [b"IHDR", b"pHYs", b"IDAT", b"IEND"]


def test_strip_is_a_noop_without_volatile_chunks():
    png = _PNG_SIGNATURE + _chunk(b"IHDR", b"\x00" * 13) + _chunk(b"IDAT", b"x") + _chunk(b"IEND")
    assert det.strip_volatile_png_chunks(png) == png


def test_strip_passes_non_png_through_untouched():
    # JPEG starts with FF D8; its resolution is pinned via normalize_image, so
    # there is nothing chunk-shaped to strip and the bytes must survive verbatim.
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 20
    assert det.strip_volatile_png_chunks(jpeg) is jpeg


def test_strip_leaves_a_truncated_tail_verbatim():
    good = _PNG_SIGNATURE + _chunk(b"IHDR", b"\x00" * 13)
    truncated = good + b"\x00\x00\x00\x40trunc"  # length claims 64 bytes that aren't there
    assert det.strip_volatile_png_chunks(truncated) == truncated


# --- normalize_image + encode_qimage: same pixels, same bytes ---------------


def _solid_image(width: int, height: int, dots_per_meter: int):
    qimage = pytest.importorskip("PySide6.QtGui").QImage
    fmt = qimage.Format.Format_RGBA8888
    image = qimage(width, height, fmt)
    image.fill(0xFF2244AA)
    image.setDotsPerMeterX(dots_per_meter)
    image.setDotsPerMeterY(dots_per_meter)
    return image


def test_normalize_pins_resolution_to_a_constant():
    image = _solid_image(4, 4, 9999)
    det.normalize_image(image)
    assert image.dotsPerMeterX() == det.FIXED_DOTS_PER_METER
    assert image.dotsPerMeterY() == det.FIXED_DOTS_PER_METER


@pytest.mark.parametrize("fmt", ["png", "jpg"])
def test_same_pixels_different_dpi_encode_identically(fmt):
    from shotquill import headless

    retina = _solid_image(6, 5, 11811)  # ~300 dpi, as a Retina grab reports
    ci_runner = _solid_image(6, 5, 3780)  # ~96 dpi
    a = headless.encode_qimage(retina, fmt, deterministic=True)
    b = headless.encode_qimage(ci_runner, fmt, deterministic=True)
    assert a == b


def test_deterministic_png_carries_no_timestamp_or_text():
    from shotquill import headless

    png = headless.encode_qimage(_solid_image(4, 4, 11811), "png", deterministic=True)
    assert not ({b"tIME", b"tEXt", b"zTXt", b"iTXt"} & set(_chunk_types(png)))
