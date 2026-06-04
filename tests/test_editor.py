# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Headless tests for the editor window's copy/save/pin/OCR actions.

OCR is driven through a fake recognizer (Apple Vision is unavailable off-Mac and
slow), so the success / empty / failure title paths are all exercised offscreen.
"""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage  # noqa: E402

from shotquill.ocr import macos as ocr_macos  # noqa: E402
from shotquill.ui.editor import EditorWindow  # noqa: E402


def _image(width=60, height=40, color="white") -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    return image


def _editor(qtbot, config):
    window = EditorWindow(_image(), config)
    qtbot.addWidget(window)
    return window


def test_copy_puts_image_on_clipboard_and_closes(qtbot, config):
    window = _editor(qtbot, config)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    window._copy()
    assert not QGuiApplication.clipboard().image().isNull()
    assert not window.isVisible()


def test_save_writes_file_and_closes(qtbot, config, tmp_path):
    config.set_save_dir(str(tmp_path))
    config.set_image_format("png")
    window = _editor(qtbot, config)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    window._save()
    saved = list(tmp_path.glob("ShotQuill *.png"))
    assert len(saved) == 1
    assert not window.isVisible()


def test_pin_emits_image_and_closes(qtbot, config):
    window = _editor(qtbot, config)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    received = []
    window.pin_requested.connect(received.append)
    window._pin()
    assert len(received) == 1
    assert isinstance(received[0], QImage)
    assert not window.isVisible()


def test_editor_places_canvas_over_capture_origin(qtbot, config):
    # The shot was taken at (120, 80) sized 300x200 logical points (the image
    # is 2x: a Retina capture). The canvas viewport must land exactly there so
    # the screenshot appears to stay in place while editing.
    origin = QRect(120, 80, 300, 200)
    window = EditorWindow(_image(600, 400), config, origin)
    qtbot.addWidget(window)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    window.show()
    qtbot.waitExposed(window)

    viewport = window._canvas.viewport()
    assert viewport.size() == origin.size()
    assert viewport.mapToGlobal(QPoint(0, 0)) == origin.topLeft()


def test_editor_near_screen_edge_is_clamped_on_screen(qtbot, config):
    # A shot taken in the bottom-right corner: snapping the canvas there would
    # push the toolbar/frame off-screen, so the window is clamped instead.
    screen = QGuiApplication.primaryScreen().availableGeometry()
    origin = QRect(screen.right() - 100, screen.bottom() - 80, 300, 200)
    window = EditorWindow(_image(600, 400), config, origin)
    qtbot.addWidget(window)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    window.show()
    qtbot.waitExposed(window)

    frame = window.frameGeometry()
    assert frame.right() <= screen.right()
    assert frame.bottom() <= screen.bottom()
    assert frame.left() >= screen.left()
    assert frame.top() >= screen.top()


def test_editor_without_origin_still_opens(qtbot, config):
    window = EditorWindow(_image(), config)
    qtbot.addWidget(window)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    window.show()
    qtbot.waitExposed(window)
    assert window.isVisible()


def _patch_recognizer(monkeypatch, *, lines=None, error=None):
    class _FakeRecognizer:
        def recognize(self, image):
            if error is not None:
                raise error
            return list(lines or [])

    monkeypatch.setattr(ocr_macos, "VisionTextRecognizer", _FakeRecognizer)


def test_ocr_success_copies_text_and_reports_count(qtbot, config, monkeypatch):
    _patch_recognizer(monkeypatch, lines=["hello", "world"])
    window = _editor(qtbot, config)
    window._ocr()
    assert QGuiApplication.clipboard().text() == "hello\nworld"
    assert window.windowTitle() == "ShotQuill — Copied 2 line(s)"


def test_ocr_empty_reports_no_text(qtbot, config, monkeypatch):
    _patch_recognizer(monkeypatch, lines=[])
    window = _editor(qtbot, config)
    window._ocr()
    assert window.windowTitle() == "ShotQuill — No text found"


def test_ocr_failure_reports_error(qtbot, config, monkeypatch):
    _patch_recognizer(monkeypatch, error=RuntimeError("boom"))
    window = _editor(qtbot, config)
    window._ocr()
    assert window.windowTitle().startswith("ShotQuill — OCR failed")
    assert "boom" in window.windowTitle()
