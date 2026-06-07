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


def _editor(qtbot, config, origin=None):
    window = EditorWindow(_image(), config, origin)
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


def test_pin_emits_image_with_origin_and_closes(qtbot, config):
    from PySide6.QtCore import QRect

    origin = QRect(10, 20, 4, 3)
    window = _editor(qtbot, config, origin=origin)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    received = []
    window.pin_requested.connect(lambda image, org: received.append((image, org)))
    window._pin()
    assert len(received) == 1
    image, got_origin = received[0]
    assert isinstance(image, QImage)
    assert got_origin == origin
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


def test_toolbar_placement_follows_the_pointer():
    # The toolbar lands in the corner nearest the pointer: (area, right-align)
    # per quadrant of the capture rect; no origin keeps the classic top-left.
    from shotquill.ui.editor import _toolbar_placement

    origin = QRect(100, 100, 200, 100)  # center (200, 150)
    assert _toolbar_placement(QPoint(120, 110), origin) == (Qt.TopToolBarArea, False)
    assert _toolbar_placement(QPoint(280, 110), origin) == (Qt.TopToolBarArea, True)
    assert _toolbar_placement(QPoint(120, 190), origin) == (Qt.BottomToolBarArea, False)
    assert _toolbar_placement(QPoint(280, 190), origin) == (Qt.BottomToolBarArea, True)
    assert _toolbar_placement(QPoint(280, 190), None) == (Qt.TopToolBarArea, False)
    assert _toolbar_placement(None, origin) == (Qt.TopToolBarArea, False)


def _fake_cursor(monkeypatch, x, y):
    """Pin the pointer position the editor reads at construction time."""
    from shotquill.ui import editor as editor_module

    class _Cursor:
        @staticmethod
        def pos():
            return QPoint(x, y)

    monkeypatch.setattr(editor_module, "QCursor", _Cursor)


def test_toolbar_moves_to_bottom_right_when_pointer_ends_there(qtbot, config, monkeypatch):
    from PySide6.QtWidgets import QToolBar, QWidgetAction

    origin = QRect(100, 100, 200, 100)
    _fake_cursor(monkeypatch, 280, 190)  # released near the bottom-right corner
    window = EditorWindow(_image(), config, origin)
    qtbot.addWidget(window)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    toolbar = window.findChild(QToolBar)
    assert window.toolBarArea(toolbar) == Qt.BottomToolBarArea
    # Right alignment comes from an expanding spacer ahead of the actions.
    assert isinstance(toolbar.actions()[0], QWidgetAction)


def test_toolbar_stays_top_left_when_pointer_ends_there(qtbot, config, monkeypatch):
    from PySide6.QtWidgets import QToolBar, QWidgetAction

    origin = QRect(100, 100, 200, 100)
    _fake_cursor(monkeypatch, 120, 110)
    window = EditorWindow(_image(), config, origin)
    qtbot.addWidget(window)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    toolbar = window.findChild(QToolBar)
    assert window.toolBarArea(toolbar) == Qt.TopToolBarArea
    assert not isinstance(toolbar.actions()[0], QWidgetAction)


def test_editor_places_canvas_over_origin_with_bottom_toolbar(qtbot, config, monkeypatch):
    # The canvas-over-capture alignment must hold with the toolbar at the
    # bottom too — placement measures the viewport, not the toolbar.
    origin = QRect(120, 80, 300, 200)
    _fake_cursor(monkeypatch, origin.right(), origin.bottom())
    window = EditorWindow(_image(600, 400), config, origin)
    qtbot.addWidget(window)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    window.show()
    qtbot.waitExposed(window)

    viewport = window._canvas.viewport()
    assert viewport.size() == origin.size()
    assert viewport.mapToGlobal(QPoint(0, 0)) == origin.topLeft()


def test_editor_opens_frameless_with_dim_backdrop_by_default(qtbot, config):
    # Spotlight mode (default): no title bar / traffic lights, and a dim layer
    # behind the editor keeps the rest of the desktop dark while editing.
    window = _editor(qtbot, config)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    assert window.windowFlags() & Qt.FramelessWindowHint
    assert window._backdrop is not None
    window.show()
    qtbot.waitExposed(window)
    assert window._backdrop.isVisible()


def test_editor_backdrop_closes_with_the_editor(qtbot, config):
    window = _editor(qtbot, config)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    window.show()
    qtbot.waitExposed(window)
    backdrop = window._backdrop
    window.close()
    assert not backdrop.isVisible()
    assert window._backdrop is None


def test_editor_backdrop_hides_while_deactivated(qtbot, config, monkeypatch):
    # Cmd-Tab away: the dim layer must not darken whatever the user switched
    # to. Coming back restores it. Activation can't be driven for real on the
    # offscreen platform, so the editor's view of it is faked and the
    # ActivationChange event delivered by hand.
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    window = _editor(qtbot, config)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    window.show()
    qtbot.waitExposed(window)

    monkeypatch.setattr(EditorWindow, "isActiveWindow", lambda self: False)
    QApplication.sendEvent(window, QEvent(QEvent.ActivationChange))
    assert not window._backdrop.isVisible()

    monkeypatch.setattr(EditorWindow, "isActiveWindow", lambda self: True)
    QApplication.sendEvent(window, QEvent(QEvent.ActivationChange))
    assert window._backdrop.isVisible()


def test_editor_backdrop_off_restores_titled_window(qtbot, config):
    config.set_editor_backdrop(False)
    window = _editor(qtbot, config)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    assert not (window.windowFlags() & Qt.FramelessWindowHint)
    assert window._backdrop is None
    assert window._status_badge is None


def test_ocr_status_shown_as_badge_in_frameless_mode(qtbot, config, monkeypatch):
    # Frameless mode has no title bar, so the OCR outcome must surface as the
    # canvas badge (the title is still set for tests/tooling).
    _patch_recognizer(monkeypatch, lines=["hi"])
    window = _editor(qtbot, config)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    window.show()
    qtbot.waitExposed(window)
    window._ocr()
    _wait_ocr_done(qtbot, window)
    assert window._status_badge.isVisible()
    assert window._status_badge.text() == window.windowTitle() == "ShotQuill — Copied 1 line(s)"


def test_editor_without_origin_still_opens(qtbot, config):
    window = EditorWindow(_image(), config)
    qtbot.addWidget(window)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    window.show()
    qtbot.waitExposed(window)
    assert window.isVisible()


def _patch_recognizer(monkeypatch, *, lines=None, error=None, started=None, release=None):
    """Install a fake recognizer; optional events observe/block the OCR thread."""

    class _FakeRecognizer:
        instances = 0

        def __init__(self):
            _FakeRecognizer.instances += 1

        def recognize(self, image):
            if started is not None:
                started.set()
            if release is not None:
                release.wait(timeout=5)
            if error is not None:
                raise error
            return list(lines or [])

    monkeypatch.setattr(ocr_macos, "VisionTextRecognizer", _FakeRecognizer)
    return _FakeRecognizer


def _wait_ocr_done(qtbot, window):
    """OCR runs on a worker thread; wait until the title leaves the running state."""
    from shotquill import i18n

    running = i18n.t("title.ocr_running")
    qtbot.waitUntil(lambda: window.windowTitle() != running, timeout=2000)


def test_ocr_success_copies_text_and_reports_count(qtbot, config, monkeypatch):
    _patch_recognizer(monkeypatch, lines=["hello", "world"])
    window = _editor(qtbot, config)
    window._ocr()
    _wait_ocr_done(qtbot, window)
    assert QGuiApplication.clipboard().text() == "hello\nworld"
    assert window.windowTitle() == "ShotQuill — Copied 2 line(s)"


def test_ocr_empty_reports_no_text(qtbot, config, monkeypatch):
    _patch_recognizer(monkeypatch, lines=[])
    window = _editor(qtbot, config)
    window._ocr()
    _wait_ocr_done(qtbot, window)
    assert window.windowTitle() == "ShotQuill — No text found"


def test_ocr_failure_reports_error(qtbot, config, monkeypatch):
    _patch_recognizer(monkeypatch, error=RuntimeError("boom"))
    window = _editor(qtbot, config)
    window._ocr()
    _wait_ocr_done(qtbot, window)
    assert window.windowTitle().startswith("ShotQuill — OCR failed")
    assert "boom" in window.windowTitle()


def test_ocr_runs_off_the_gui_thread_and_ignores_reentry(qtbot, config, monkeypatch):
    # _ocr must return immediately (recognition on a worker thread, title in
    # the running state) and a second click while in flight must not start a
    # second recognizer.
    import threading

    started = threading.Event()
    release = threading.Event()
    recognizer = _patch_recognizer(monkeypatch, lines=["hi"], started=started, release=release)
    window = _editor(qtbot, config)

    window._ocr()  # returns without blocking even though recognize() is stuck
    assert window.windowTitle() == "ShotQuill — Recognizing text…"
    assert started.wait(timeout=2)
    window._ocr()  # ignored: one OCR already in flight
    release.set()
    _wait_ocr_done(qtbot, window)
    assert recognizer.instances == 1
    assert window.windowTitle() == "ShotQuill — Copied 1 line(s)"
