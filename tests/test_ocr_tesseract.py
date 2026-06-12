# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the Linux Tesseract OCR backend (subprocess is faked throughout)."""

from __future__ import annotations

import subprocess

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QImage  # noqa: E402

from shotquill.ocr import linux  # noqa: E402


def _image() -> QImage:
    image = QImage(4, 3, QImage.Format.Format_RGB888)
    image.fill(0xFFFFFF)
    return image


class _FakeProc:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_recognize_parses_lines_and_drops_blanks(monkeypatch):
    monkeypatch.setattr(linux, "tesseract_path", lambda: "/usr/bin/tesseract")
    monkeypatch.setattr(linux, "_installed_languages", lambda binary: {"eng"})

    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["input"] = kwargs.get("input")
        # Tesseract pads output with blank lines and a trailing form feed.
        return _FakeProc(stdout=b"hello\n\nworld \n\x0c")

    monkeypatch.setattr(linux.subprocess, "run", fake_run)

    lines = linux.TesseractTextRecognizer().recognize(_image())

    assert lines == ["hello", "world"]
    assert captured["args"][:3] == ["/usr/bin/tesseract", "stdin", "stdout"]
    # Image is piped on stdin as PNG bytes (no temp file).
    assert captured["input"][:8] == b"\x89PNG\r\n\x1a\n"


def test_recognize_requests_only_installed_languages(monkeypatch):
    monkeypatch.setattr(linux, "tesseract_path", lambda: "tesseract")
    # chi_sim is requested by default but not installed here.
    monkeypatch.setattr(linux, "_installed_languages", lambda binary: {"eng", "deu"})

    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return _FakeProc(stdout=b"text\n")

    monkeypatch.setattr(linux.subprocess, "run", fake_run)

    linux.TesseractTextRecognizer().recognize(_image())

    assert "-l" in captured["args"]
    assert captured["args"][captured["args"].index("-l") + 1] == "eng"


def test_recognize_omits_lang_flag_when_none_installed(monkeypatch):
    monkeypatch.setattr(linux, "tesseract_path", lambda: "tesseract")
    monkeypatch.setattr(linux, "_installed_languages", lambda binary: set())

    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return _FakeProc(stdout=b"text\n")

    monkeypatch.setattr(linux.subprocess, "run", fake_run)

    linux.TesseractTextRecognizer().recognize(_image())

    assert "-l" not in captured["args"]


def test_recognize_raises_when_not_installed(monkeypatch):
    monkeypatch.setattr(linux, "tesseract_path", lambda: None)
    with pytest.raises(RuntimeError, match="not installed"):
        linux.TesseractTextRecognizer().recognize(_image())


def test_recognize_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(linux, "tesseract_path", lambda: "tesseract")
    monkeypatch.setattr(linux, "_installed_languages", lambda binary: {"eng"})
    monkeypatch.setattr(
        linux.subprocess,
        "run",
        lambda args, **kwargs: _FakeProc(stderr=b"boom", returncode=1),
    )
    with pytest.raises(RuntimeError, match="boom"):
        linux.TesseractTextRecognizer().recognize(_image())


def test_recognize_wraps_subprocess_error(monkeypatch):
    monkeypatch.setattr(linux, "tesseract_path", lambda: "tesseract")
    monkeypatch.setattr(linux, "_installed_languages", lambda binary: {"eng"})

    def boom(args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="tesseract", timeout=120)

    monkeypatch.setattr(linux.subprocess, "run", boom)
    with pytest.raises(RuntimeError, match="tesseract failed"):
        linux.TesseractTextRecognizer().recognize(_image())


def test_installed_languages_parses_list(monkeypatch):
    def fake_run(args, **kwargs):
        assert args[1] == "--list-langs"
        # --list-langs is run with text=True, so stdout is already decoded.
        return _FakeProc(stdout="List of available languages:\neng\nchi_sim\n")

    monkeypatch.setattr(linux.subprocess, "run", fake_run)
    assert linux._installed_languages("tesseract") == {"eng", "chi_sim"}


def test_installed_languages_empty_on_error(monkeypatch):
    def boom(args, **kwargs):
        raise OSError("no binary")

    monkeypatch.setattr(linux.subprocess, "run", boom)
    assert linux._installed_languages("tesseract") == set()
