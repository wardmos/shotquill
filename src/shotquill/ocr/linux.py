# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Linux OCR via the Tesseract CLI (on-device, offline, free).

Tesseract is invoked as a subprocess rather than through a Python binding, so
the backend adds no install-time dependency: it works the moment the system
``tesseract`` package is present and stays dormant (``get_recognizer`` returns
``None``) when it is not. The binary and its language data are discovered lazily
so this module imports cleanly even where Tesseract is absent (e.g. CI).
"""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

from shotquill.ocr.base import TextBox, TextRecognizer

if TYPE_CHECKING:
    from PySide6.QtGui import QImage

# Mirror the macOS backend's defaults, but in Tesseract's own language codes:
# Simplified Chinese + English. Whichever of these has training data installed
# is used; the rest are dropped so a missing pack never fails the whole run.
_DEFAULT_LANGUAGES = ("chi_sim", "eng")

_BINARY = "tesseract"

# Installed languages per binary path, cached for the process lifetime. Probing
# spawns a `tesseract --list-langs` subprocess and the answer only changes when
# the user installs or removes a language pack — rare enough that caching is safe
# and spares a second spawn on every recognize() call. Only successful (non-empty)
# probes are cached, so a transient failure is retried rather than poisoning the
# whole session.
_LANGUAGE_CACHE: dict[str, set[str]] = {}


def tesseract_path() -> str | None:
    """Absolute path to the ``tesseract`` binary, or ``None`` when not installed."""
    return shutil.which(_BINARY)


def _probe_languages(binary: str) -> set[str]:
    """Ask Tesseract which languages it has training data for (empty on failure)."""
    try:
        proc = subprocess.run(
            [binary, "--list-langs"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    # Tesseract prints a human-readable header ("List of available languages…")
    # then one language code per line. Some builds/locales send the header to
    # stderr, so dropping stdout line 0 positionally can swallow a real code
    # (e.g. chi_sim). Filter by shape instead: a code is a single whitespace-free
    # token, while the header line always contains spaces.
    langs: set[str] = set()
    for stream in (proc.stdout, proc.stderr):
        for line in stream.splitlines():
            token = line.strip()
            if token and len(token.split()) == 1:
                langs.add(token)
    return langs


def _installed_languages(binary: str) -> set[str]:
    """Cached view of :func:`_probe_languages`, keyed by binary path."""
    cached = _LANGUAGE_CACHE.get(binary)
    if cached is not None:
        return cached
    langs = _probe_languages(binary)
    if langs:
        _LANGUAGE_CACHE[binary] = langs
    return langs


# The TSV columns Tesseract emits with the ``tsv`` config, in fixed order. We
# look them up by name from the header rather than by position, so a layout
# change in some build degrades to "no boxes" instead of misreading coordinates.
_TSV_COLUMNS = (
    "level",
    "block_num",
    "par_num",
    "line_num",
    "word_num",
    "left",
    "top",
    "width",
    "height",
    "conf",
    "text",
)

# Tesseract's hierarchy level for a single word row; only these carry text and a
# tight box. Page/block/paragraph/line rows (levels 1–4) have empty text, so we
# rebuild each line by grouping its word rows.
_TSV_LEVEL_WORD = "5"


def boxes_from_tsv(tsv: str) -> list[TextBox]:
    """Parse Tesseract ``tsv`` output into per-line :class:`TextBox` spans (pure).

    Word rows are grouped back into lines by their ``(block, par, line)`` key, the
    words joined in ``word_num`` order, and the line box taken as the union of its
    word boxes — so the text matches what the plain-text output would give while
    the box locates it. Rows with negative confidence (Tesseract's marker for "no
    word here") or empty text are dropped. Lines come back in reading order
    (top-to-bottom, then left-to-right). An unrecognized header degrades to ``[]``.
    """
    rows = tsv.splitlines()
    if not rows:
        return []
    header = rows[0].split("\t")
    try:
        idx = {name: header.index(name) for name in _TSV_COLUMNS}
    except ValueError:
        return []  # unexpected TSV shape — better no boxes than wrong ones

    # Group word rows into lines, preserving first-seen order for a stable result.
    grouped: dict[tuple[str, str, str], list[tuple[int, str, int, int, int, int]]] = {}
    order: list[tuple[str, str, str]] = []
    for row in rows[1:]:
        cols = row.split("\t")
        if len(cols) <= idx["text"] or cols[idx["level"]] != _TSV_LEVEL_WORD:
            continue
        text = cols[idx["text"]].strip()
        if not text:
            continue
        try:
            conf = float(cols[idx["conf"]])
            word_num = int(cols[idx["word_num"]])
            left, top = int(cols[idx["left"]]), int(cols[idx["top"]])
            width, height = int(cols[idx["width"]]), int(cols[idx["height"]])
        except ValueError:
            continue
        if conf < 0:
            continue
        key = (cols[idx["block_num"]], cols[idx["par_num"]], cols[idx["line_num"]])
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append((word_num, text, left, top, width, height))

    boxes: list[TextBox] = []
    for key in order:
        words = sorted(grouped[key])
        text = " ".join(w[1] for w in words)
        x0 = min(w[2] for w in words)
        y0 = min(w[3] for w in words)
        x1 = max(w[2] + w[4] for w in words)
        y1 = max(w[3] + w[5] for w in words)
        boxes.append(TextBox(text=text, x=x0, y=y0, width=x1 - x0, height=y1 - y0))
    boxes.sort(key=lambda b: (b.y, b.x))
    return boxes


class TesseractTextRecognizer(TextRecognizer):
    """Recognise text by piping a PNG of the image through ``tesseract``."""

    backend_name = "Tesseract"

    def recognize_boxes(self, image: QImage) -> list[TextBox]:
        from shotquill.headless import CapabilityUnsupported

        binary = tesseract_path()
        if binary is None:
            # The factory already gated on this, but a teardown between then and
            # now should still raise the typed, exit-coded error the contract
            # documents (exit 4) rather than a generic RuntimeError.
            raise CapabilityUnsupported("ocr", "tesseract is not installed")

        from PySide6.QtCore import QBuffer, QByteArray, QIODevice

        # QImage -> PNG bytes, fed to tesseract on stdin (no temp file).
        buffer_bytes = QByteArray()
        buffer = QBuffer(buffer_bytes)
        buffer.open(QIODevice.WriteOnly)
        image.save(buffer, "PNG")
        buffer.close()

        args = [binary, "stdin", "stdout"]
        # Only request languages whose data is actually installed; if none match,
        # omit -l entirely and let Tesseract fall back to its built-in default.
        installed = _installed_languages(binary)
        wanted = [lang for lang in _DEFAULT_LANGUAGES if lang in installed]
        if wanted:
            args += ["-l", "+".join(wanted)]
        # The ``tsv`` config asks Tesseract for per-word boxes + confidence instead
        # of plain text. It must follow the options, so it goes last.
        args.append("tsv")

        try:
            proc = subprocess.run(
                args,
                input=bytes(buffer_bytes),
                capture_output=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"tesseract failed: {exc}") from exc
        if proc.returncode != 0:
            detail = proc.stderr.decode("utf-8", "replace").strip() or "unknown error"
            raise RuntimeError(f"tesseract failed: {detail}")

        return boxes_from_tsv(proc.stdout.decode("utf-8", "replace"))
