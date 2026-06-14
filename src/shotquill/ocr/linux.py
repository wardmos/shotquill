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

from shotquill.ocr.base import TextRecognizer

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


class TesseractTextRecognizer(TextRecognizer):
    """Recognise text by piping a PNG of the image through ``tesseract``."""

    backend_name = "Tesseract"

    def recognize(self, image: QImage) -> list[str]:
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

        text = proc.stdout.decode("utf-8", "replace")
        # Tesseract emits text already in reading order (top-to-bottom); drop the
        # blank lines and trailing form feed it pads pages with.
        return [line.strip() for line in text.splitlines() if line.strip()]
