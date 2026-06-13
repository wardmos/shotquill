# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Normalize an encoded capture so identical pixels yield identical bytes.

A screenshot used as a test artifact (golden image, pixel diff, content hash)
is only useful if the *same* pixels always encode to the *same* bytes. Two
things break that without touching a single visible pixel:

- **Embedded resolution.** Qt copies the source image's dots-per-meter into the
  PNG ``pHYs`` chunk (and the JPEG JFIF density). That value tracks the
  capturing display, so the byte stream differs between a Retina Mac and a 96-DPI
  CI runner even when the frames are pixel-for-pixel identical.
- **Timestamp / text chunks.** PNG can carry a ``tIME`` chunk and ``tEXt`` /
  ``zTXt`` / ``iTXt`` text chunks. Current Qt does not emit them, but a newer Qt
  or a platform image handler could, and a wall-clock ``tIME`` would change every
  run. Stripping them makes the determinism guarantee hold regardless of handler.

This module is pure bytes/Qt-metadata maths with no capture dependency, so it is
unit-testable without a screen.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtGui import QImage

# A fixed physical resolution stamped into every deterministic frame, replacing
# whatever the capturing display reported. 2835 dots/m == 72 dpi, the
# conventional screen baseline; the exact value is irrelevant — only that it is
# constant — but a sane one keeps the file honest if something reads the DPI.
FIXED_DOTS_PER_METER = 2835

# The 8-byte PNG signature every PNG starts with.
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# PNG ancillary chunks whose contents can vary run-to-run (clocks) or by
# environment (tool/host text). Dropped from deterministic output; every other
# chunk — IHDR, PLTE, IDAT, IEND, pHYs (already normalized below), tRNS, … — is
# preserved so the image still decodes identically.
_VOLATILE_PNG_CHUNKS = frozenset((b"tIME", b"tEXt", b"zTXt", b"iTXt"))


def normalize_image(image: QImage) -> QImage:
    """Return ``image`` with environment-dependent metadata pinned to constants.

    Only the embedded resolution is metadata Qt carries on the ``QImage`` itself;
    the pixels are untouched. Mutates and returns the same object — callers pass a
    capture they already own.
    """
    image.setDotsPerMeterX(FIXED_DOTS_PER_METER)
    image.setDotsPerMeterY(FIXED_DOTS_PER_METER)
    return image


def strip_volatile_png_chunks(data: bytes) -> bytes:
    """Remove timestamp / text chunks from a PNG byte stream.

    Returns ``data`` unchanged when it is not a PNG (e.g. JPEG, whose resolution
    is already pinned via :func:`normalize_image`) or when nothing volatile is
    present, so it is safe to call on any encoded output.
    """
    if not data.startswith(_PNG_SIGNATURE):
        return data
    out = bytearray(_PNG_SIGNATURE)
    i = len(_PNG_SIGNATURE)
    n = len(data)
    while i + 8 <= n:
        length = int.from_bytes(data[i : i + 4], "big")
        chunk_type = data[i + 4 : i + 8]
        end = i + 12 + length  # 4 length + 4 type + length data + 4 CRC
        if end > n:
            # Truncated/garbage tail — copy the rest verbatim rather than guess,
            # so a malformed input is never silently rewritten into something
            # that claims to be clean.
            out.extend(data[i:])
            return bytes(out)
        if chunk_type not in _VOLATILE_PNG_CHUNKS:
            out.extend(data[i:end])
        i = end
    out.extend(data[i:])
    return bytes(out)
