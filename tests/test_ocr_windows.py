# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Windows OCR: pure line extraction and the platform-factory wiring.

The WinRT shim (image conversion + the async OCR call) needs a real Windows
session with OCR language packs, which the test platform lacks — it is exercised
only on Windows. The decision worth testing without an engine (how an
``OcrResult`` becomes ordered text lines) lives in ``lines_from_result``.
"""

from __future__ import annotations

from types import SimpleNamespace

from shotquill import headless, ocr
from shotquill.ocr import windows as ocr_windows

# --- lines_from_result (pure) -------------------------------------------------


def _result(*texts):
    return SimpleNamespace(lines=[SimpleNamespace(text=t) for t in texts])


def test_lines_in_reading_order_are_collected():
    assert ocr_windows.lines_from_result(_result("first", "second")) == ["first", "second"]


def test_blank_and_whitespace_lines_are_dropped():
    assert ocr_windows.lines_from_result(_result("  keep ", "", "   ")) == ["keep"]


def test_none_line_list_yields_empty():
    # An image with no text comes back with lines=None; must not blow up.
    assert ocr_windows.lines_from_result(SimpleNamespace(lines=None)) == []


def test_backend_name_is_windows_ocr():
    assert ocr_windows.WindowsOcrRecognizer.backend_name == "Windows OCR"


# --- is_available probe -------------------------------------------------------


def test_is_available_false_when_winrt_missing():
    # The WinRT projection isn't installed in the test env (and never on Linux),
    # so the probe must report unavailable rather than raise.
    assert ocr_windows.is_available() is False


# --- factory routing ----------------------------------------------------------


def test_gui_factory_returns_recognizer_when_available(monkeypatch):
    monkeypatch.setattr(ocr.sys, "platform", "win32")
    monkeypatch.setattr(ocr_windows, "is_available", lambda: True)
    assert isinstance(ocr.get_recognizer(), ocr_windows.WindowsOcrRecognizer)


def test_gui_factory_returns_none_when_unavailable(monkeypatch):
    monkeypatch.setattr(ocr.sys, "platform", "win32")
    monkeypatch.setattr(ocr_windows, "is_available", lambda: False)
    assert ocr.get_recognizer() is None


def test_headless_factory_returns_recognizer_when_available(monkeypatch):
    monkeypatch.setattr(headless.sys, "platform", "win32")
    monkeypatch.setattr(ocr_windows, "is_available", lambda: True)
    assert isinstance(headless.get_recognizer(), ocr_windows.WindowsOcrRecognizer)


def test_headless_factory_raises_with_install_hint_when_unavailable(monkeypatch):
    monkeypatch.setattr(headless.sys, "platform", "win32")
    monkeypatch.setattr(ocr_windows, "is_available", lambda: False)
    try:
        headless.get_recognizer()
    except headless.CapabilityUnsupported as exc:
        assert "windows-ocr" in exc.reason  # actionable pip extra
    else:
        raise AssertionError("expected CapabilityUnsupported")


def test_doctor_reports_backend_name(monkeypatch):
    # The doctor surfaces which engine answered; on Windows that's "Windows OCR".
    # Short-circuit the capture probes (Qt + real Win32) so this isolates the
    # ocr check — those paths have their own tests.
    monkeypatch.setattr(headless.sys, "platform", "win32")
    monkeypatch.setattr(ocr_windows, "is_available", lambda: True)

    def _no_capture(*a, **kw):
        raise headless.CapabilityUnsupported("capture", "stubbed for the ocr test")

    monkeypatch.setattr(headless, "get_capturer", _no_capture)
    checks = headless.doctor_checks()
    ocr_check = next(c for c in checks if c["capability"] == "ocr")
    assert ocr_check["available"] is True
    assert ocr_check["detail"] == "Windows OCR"
