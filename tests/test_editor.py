# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Headless tests for the editor window's copy/save/pin/OCR actions.

OCR is driven through a fake recognizer (Apple Vision is unavailable off-Mac and
slow), so the success / empty / failure title paths are all exercised offscreen.
"""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QKeySequence  # noqa: E402

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


def test_save_failure_keeps_editor_open(qtbot, config, monkeypatch):
    # A failed write (full disk, unwritable folder) must warn and leave the
    # editor open so the annotations aren't lost.
    from PySide6.QtWidgets import QMessageBox

    from shotquill.output import saver

    def _failing_save(image, directory, image_format="png"):
        raise OSError("disk full")

    monkeypatch.setattr(saver, "save_qimage", _failing_save)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))

    window = _editor(qtbot, config)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    window.show()
    window._save()
    assert window.isVisible()
    assert len(warnings) == 1
    assert "disk full" in str(warnings[0])


def test_pin_emits_image_and_closes(qtbot, config):
    window = _editor(qtbot, config)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    received = []
    window.pin_requested.connect(received.append)
    window._pin()
    assert len(received) == 1
    assert isinstance(received[0], QImage)
    assert not window.isVisible()


def test_space_copies_to_clipboard_and_closes(qtbot, config):
    QGuiApplication.clipboard().clear()
    window = _editor(qtbot, config)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    qtbot.keyClick(window, Qt.Key_Space)
    assert not QGuiApplication.clipboard().image().isNull()
    assert not window.isVisible()


def test_enter_saves_to_folder_and_closes(qtbot, config, tmp_path):
    config.set_save_dir(str(tmp_path))
    config.set_image_format("png")
    window = _editor(qtbot, config)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    qtbot.keyClick(window, Qt.Key_Return)
    assert len(list(tmp_path.glob("ShotQuill *.png"))) == 1
    assert not window.isVisible()


def test_custom_finish_keys_are_honoured(qtbot, config, tmp_path):
    config.set_save_dir(str(tmp_path))
    config.set_editor_hotkey("editor_save", "Ctrl+D")
    window = _editor(qtbot, config)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    # The old default no longer saves...
    qtbot.keyClick(window, Qt.Key_Return)
    assert list(tmp_path.glob("ShotQuill *.png")) == []
    # ...the remapped combo does.
    qtbot.keyClick(window, Qt.Key_D, Qt.ControlModifier)
    assert len(list(tmp_path.glob("ShotQuill *.png"))) == 1
    assert not window.isVisible()


def test_finish_key_names_shown_in_toolbar_tooltips(qtbot, config):
    window = _editor(qtbot, config)
    assert window._copy_action.toolTip() == "Copy to clipboard (Space)"
    assert window._save_action.toolTip() == "Save to file (Enter)"


def test_finish_tip_localizes_key_via_portable_name():
    # The localized name must be looked up by the key's *portable* spelling and
    # swapped into the NativeText rendering: on macOS NativeText shows Return
    # as ↩ (never "Return"), so matching on the native string would silently
    # skip localization there. Native renderings differ per platform (Ctrl+D
    # is ⌘D on macOS), so expectations are built from NativeText, not literals.
    from PySide6.QtGui import QKeySequence

    from shotquill import i18n
    from shotquill.ui.editor import _finish_tip

    native_ctrl_d = QKeySequence("Ctrl+D").toString(QKeySequence.NativeText)
    native_ctrl = native_ctrl_d[:-1]  # the native Ctrl prefix: "Ctrl+" or "⌘"
    try:
        i18n.set_language("zh")
        assert _finish_tip(QKeySequence("Return"), "保存") == "保存 (回车)"
        assert _finish_tip(QKeySequence("Ctrl+Return"), "保存") == f"保存 ({native_ctrl}回车)"
        # Unknown keys pass through untouched; empty means the key is off.
        assert _finish_tip(QKeySequence("Ctrl+D"), "保存") == f"保存 ({native_ctrl_d})"
        assert _finish_tip(QKeySequence(), "保存") == "保存"
    finally:
        i18n.set_language(i18n.DEFAULT_LANGUAGE)


def test_reload_finish_keys_applies_new_bindings_to_open_editor(qtbot, config, tmp_path):
    config.set_save_dir(str(tmp_path))
    window = _editor(qtbot, config)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    # Simulate the user remapping save in Settings while this editor is open.
    config.set_editor_hotkey("editor_save", "Ctrl+D")
    window.reload_finish_keys()
    qtbot.keyClick(window, Qt.Key_Return)  # old binding is inert
    assert list(tmp_path.glob("ShotQuill *.png")) == []
    qtbot.keyClick(window, Qt.Key_D, Qt.ControlModifier)  # new binding works
    assert len(list(tmp_path.glob("ShotQuill *.png"))) == 1
    # Tooltips use NativeText, which renders Ctrl+D as ⌘D on macOS.
    native = QKeySequence("Ctrl+D").toString(QKeySequence.NativeText)
    assert native in window._save_action.toolTip()


def test_disabled_finish_keys_do_nothing(qtbot, config, tmp_path):
    QGuiApplication.clipboard().clear()
    config.set_save_dir(str(tmp_path))
    config.set_hotkey_enabled("editor_copy", False)
    config.set_hotkey_enabled("editor_save", False)
    window = _editor(qtbot, config)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    qtbot.keyClick(window, Qt.Key_Space)
    qtbot.keyClick(window, Qt.Key_Return)
    assert QGuiApplication.clipboard().image().isNull()
    assert list(tmp_path.glob("ShotQuill *.png")) == []


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
