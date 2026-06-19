# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the Linux Tesseract OCR backend (subprocess is faked throughout)."""

from __future__ import annotations

import subprocess

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QImage  # noqa: E402

from shotquill.ocr import linux  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_language_cache():
    # The installed-language cache lives for the process; reset it around each
    # test so a cached probe from one case can't leak into the next.
    linux._LANGUAGE_CACHE.clear()
    yield
    linux._LANGUAGE_CACHE.clear()


def _image() -> QImage:
    image = QImage(4, 3, QImage.Format.Format_RGB888)
    image.fill(0xFFFFFF)
    return image


class _FakeProc:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


_TSV_HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
)


def _tsv(*rows: str) -> bytes:
    """A minimal Tesseract ``tsv`` document: header + the given raw rows."""
    return ("\n".join([_TSV_HEADER, *rows]) + "\n").encode("utf-8")


def test_recognize_parses_lines_and_boxes(monkeypatch):
    monkeypatch.setattr(linux, "tesseract_path", lambda: "/usr/bin/tesseract")
    monkeypatch.setattr(linux, "_installed_languages", lambda binary: {"eng"})

    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["input"] = kwargs.get("input")
        # Two words on line 1 join into one box; a second line follows. The "-1"
        # confidence rows (Tesseract's page/line scaffolding) must be ignored.
        return _FakeProc(
            stdout=_tsv(
                "4\t1\t1\t1\t1\t0\t10\t10\t100\t20\t-1\t",
                "5\t1\t1\t1\t1\t1\t10\t10\t40\t20\t96\thello",
                "5\t1\t1\t1\t1\t2\t60\t12\t50\t18\t95\tworld",
                "5\t1\t1\t1\t2\t1\t10\t50\t30\t16\t90\tbye",
            )
        )

    monkeypatch.setattr(linux.subprocess, "run", fake_run)

    boxes = linux.TesseractTextRecognizer().recognize_boxes(_image())

    # Line 1 = both words joined; its box is the union (x:10..110, y:10..30).
    assert [(b.text, b.as_rect()) for b in boxes] == [
        ("hello world", (10, 10, 100, 20)),
        ("bye", (10, 50, 30, 16)),
    ]
    # recognize() is the text-only view of the same call.
    assert linux.TesseractTextRecognizer().recognize(_image()) == ["hello world", "bye"]
    assert captured["args"][:3] == ["/usr/bin/tesseract", "stdin", "stdout"]
    assert captured["args"][-1] == "tsv"  # box output requested
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


def test_recognize_raises_unsupported_when_not_installed(monkeypatch):
    from shotquill import headless

    monkeypatch.setattr(linux, "tesseract_path", lambda: None)
    with pytest.raises(headless.CapabilityUnsupported, match="not installed") as exc:
        linux.TesseractTextRecognizer().recognize(_image())
    assert exc.value.exit_code == headless.EXIT_UNSUPPORTED


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


def test_probe_languages_parses_list(monkeypatch):
    def fake_run(args, **kwargs):
        assert args[1] == "--list-langs"
        # --list-langs is run with text=True, so stdout is already decoded.
        return _FakeProc(stdout="List of available languages:\neng\nchi_sim\n")

    monkeypatch.setattr(linux.subprocess, "run", fake_run)
    assert linux._probe_languages("tesseract") == {"eng", "chi_sim"}


def test_probe_languages_keeps_codes_when_header_goes_to_stderr(monkeypatch):
    # Some Tesseract builds print the "List of available languages" banner to
    # stderr; dropping stdout line 0 positionally would then swallow a real code.
    def fake_run(args, **kwargs):
        return _FakeProc(
            stdout="eng\nchi_sim\n",
            stderr="List of available languages (2):\n",
        )

    monkeypatch.setattr(linux.subprocess, "run", fake_run)
    assert linux._probe_languages("tesseract") == {"eng", "chi_sim"}


def test_probe_languages_empty_on_error(monkeypatch):
    def boom(args, **kwargs):
        raise OSError("no binary")

    monkeypatch.setattr(linux.subprocess, "run", boom)
    assert linux._probe_languages("tesseract") == set()


def test_installed_languages_caches_successful_probe(monkeypatch):
    calls = {"n": 0}

    def fake_run(args, **kwargs):
        calls["n"] += 1
        return _FakeProc(stdout="List of available languages:\neng\n")

    monkeypatch.setattr(linux.subprocess, "run", fake_run)
    assert linux._installed_languages("tesseract") == {"eng"}
    assert linux._installed_languages("tesseract") == {"eng"}
    # Second call is served from the cache — no extra subprocess spawn.
    assert calls["n"] == 1


def test_installed_languages_does_not_cache_failure(monkeypatch):
    calls = {"n": 0}

    def fake_run(args, **kwargs):
        calls["n"] += 1
        raise OSError("transient")

    monkeypatch.setattr(linux.subprocess, "run", fake_run)
    assert linux._installed_languages("tesseract") == set()
    # A transient failure is retried rather than cached for the whole session.
    assert linux._installed_languages("tesseract") == set()
    assert calls["n"] == 2
