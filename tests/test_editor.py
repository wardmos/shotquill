# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Headless tests for the editor window's copy/save/pin/OCR actions.

OCR is driven through a fake recognizer (Apple Vision is unavailable off-Mac and
slow), so the success / empty / failure title paths are all exercised offscreen.
"""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QRect, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QKeySequence  # noqa: E402
from PySide6.QtWidgets import QGraphicsItem, QGraphicsRectItem  # noqa: E402

from shotquill.ui import editor as editor_module  # noqa: E402
from shotquill.ui.canvas import _AddItemCommand  # noqa: E402
from shotquill.ui.editor import EditorWindow, RegionContext  # noqa: E402


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


def test_delete_key_removes_selected_annotation_when_editor_has_focus(qtbot, config):
    window = _editor(qtbot, config)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    item = QGraphicsRectItem(QRectF(5, 5, 20, 20))
    item.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable)
    window._canvas.scene().addItem(item)
    window._canvas.undo_stack().push(_AddItemCommand(window._canvas.scene(), item))
    item.setSelected(True)

    window.setFocus()
    qtbot.keyClick(window, Qt.Key_Delete)

    assert item.scene() is None
    window._canvas.undo_stack().undo()
    assert item.scene() is window._canvas.scene()


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


def test_near_screen_sized_capture_keeps_bottom_toolbar_on_screen(qtbot, config, monkeypatch):
    # A capture covering (almost) the whole screen: the viewport alone fills
    # the available area, so adding the toolbar/frame chrome must shrink the
    # window — not push the toolbar off the bottom edge, which is exactly
    # where the pointer put it after a drag to the bottom-right corner.
    from PySide6.QtWidgets import QToolBar

    monkeypatch.setattr(editor_module, "_MAX_INITIAL_WIDTH", 100_000)
    monkeypatch.setattr(editor_module, "_MAX_INITIAL_HEIGHT", 100_000)
    available = QGuiApplication.primaryScreen().availableGeometry()
    _fake_cursor(monkeypatch, available.right(), available.bottom())
    window = EditorWindow(_image(600, 400), config, QRect(available))
    qtbot.addWidget(window)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    window.show()
    qtbot.waitExposed(window)

    frame = window.frameGeometry()
    assert frame.bottom() <= available.bottom()
    assert frame.right() <= available.right()
    toolbar = window.findChild(QToolBar)
    toolbar_bottom = toolbar.mapToGlobal(QPoint(0, toolbar.height() - 1)).y()
    assert toolbar_bottom <= available.bottom()


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

    class _Cursor:
        @staticmethod
        def pos():
            return QPoint(x, y)

    monkeypatch.setattr(editor_module, "QCursor", _Cursor)


def test_toolbar_moves_to_bottom_right_when_pointer_ends_there(qtbot, config, monkeypatch):
    from PySide6.QtWidgets import QWidgetAction

    origin = QRect(100, 100, 200, 100)
    _fake_cursor(monkeypatch, 280, 190)  # released near the bottom-right corner
    window = EditorWindow(_image(), config, origin)
    qtbot.addWidget(window)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    toolbar = window._toolbar
    outputs = toolbar.outputs_toolbar
    # The tool row follows the pointer; the no-collapse copy/save bar takes the
    # opposite edge so the two never share (and widen) a row.
    assert window.toolBarArea(toolbar) == Qt.BottomToolBarArea
    assert window.toolBarArea(outputs) == Qt.TopToolBarArea
    # Right alignment pushes the copy/save bar's buttons to the trailing edge via
    # an expanding spacer ahead of its actions.
    assert isinstance(outputs.actions()[0], QWidgetAction)


def test_copy_and_save_stay_visible_when_the_editor_is_narrow(qtbot, config, monkeypatch):
    # The shot's finish buttons must never fold behind the tool row's overflow
    # chevron: on a capture far narrower than the full row, the tool buttons fold
    # but copy/save ride a no-collapse sibling bar that keeps them on the row.
    _fake_cursor(monkeypatch, 120, 110)
    window = EditorWindow(_image(), config, QRect(100, 100, 200, 100))
    qtbot.addWidget(window)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    window.resize(140, 300)
    window.show()
    qtbot.waitExposed(window)
    toolbar = window._toolbar
    outputs = toolbar.outputs_toolbar
    # The tool row has folded (its trailing button is hidden behind the chevron)...
    assert not toolbar.widgetForAction(toolbar.actions()[-1]).isVisible()
    # ...yet copy and save are still shown.
    assert outputs.widgetForAction(window._copy_action).isVisible()
    assert outputs.widgetForAction(window._save_action).isVisible()


def test_outputs_take_the_opposite_edge_and_dont_widen_a_narrow_shot(qtbot, config):
    # The no-collapse outputs bar sits in the opposite edge's area, so the window
    # minimum width is the wider single bar rather than the sum of both — a narrow
    # shot keeps its width (the canvas stays exactly over the capture) and copy/
    # save stay visible.
    origin = QRect(100, 100, 80, 100)  # narrower than the tool row plus outputs
    screenshot = _image(400, 300)
    window = EditorWindow(
        screenshot.copy(origin), config, origin, RegionContext(screenshot, QRect(0, 0, 400, 300))
    )
    qtbot.addWidget(window)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    window.show()
    qtbot.waitExposed(window)
    toolbar = window._toolbar
    outputs = toolbar.outputs_toolbar
    assert window.toolBarArea(toolbar) != window.toolBarArea(outputs)
    assert window._canvas.viewport().size() == origin.size()
    assert outputs.widgetForAction(window._copy_action).isVisible()
    assert outputs.widgetForAction(window._save_action).isVisible()


def test_toolbar_stays_top_left_when_pointer_ends_there(qtbot, config, monkeypatch):
    from PySide6.QtWidgets import QWidgetAction

    origin = QRect(100, 100, 200, 100)
    _fake_cursor(monkeypatch, 120, 110)
    window = EditorWindow(_image(), config, origin)
    qtbot.addWidget(window)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    toolbar = window._toolbar
    outputs = toolbar.outputs_toolbar
    assert window.toolBarArea(toolbar) == Qt.TopToolBarArea
    assert window.toolBarArea(outputs) == Qt.BottomToolBarArea
    assert not isinstance(outputs.actions()[0], QWidgetAction)


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


def test_no_ocr_action_when_platform_has_no_recognizer(qtbot, config, monkeypatch):
    # On Linux get_recognizer() returns None; the editor must omit the OCR
    # button and _ocr() must no-op rather than touch a missing recognizer.
    from PySide6.QtWidgets import QToolBar

    monkeypatch.setattr(editor_module, "get_recognizer", lambda: None)
    window = _editor(qtbot, config)
    toolbar = window.findChild(QToolBar)
    assert "Copy Text" not in {a.text() for a in toolbar.actions()}
    window._ocr()  # must not raise even though there is no recognizer
    assert window._recognizer is None


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

    # Patch the platform factory the editor calls at construction, so the fake
    # is installed regardless of host OS (macOS Vision is unavailable off-Mac).
    monkeypatch.setattr(editor_module, "get_recognizer", lambda: _FakeRecognizer())
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


# --- crop adjustment (merged adjust+annotate mode) ---------------------------


def _region_editor(qtbot, config, native=(100, 50), logical=(100, 50), screenshot=None):
    """An editor opened from a region capture: origin (10, 10, 30, 20) cropped
    out of a full-desktop screenshot, with the RegionContext to re-crop from."""
    screenshot = screenshot if screenshot is not None else _image(*native)
    geometry = QRect(0, 0, *logical)
    origin = QRect(10, 10, 30, 20)
    window = EditorWindow(_image(), config, origin, RegionContext(screenshot, geometry))
    qtbot.addWidget(window)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    return window


def test_arrow_moves_crop_and_recrops_from_screenshot(qtbot, config):
    # sx = sy = 1: one native pixel is one logical point. Mark the screenshot
    # pixel the moved crop's top-left should land on.
    screenshot = _image(100, 50)
    screenshot.setPixelColor(11, 10, QColor("red"))
    window = _region_editor(qtbot, config, screenshot=screenshot)

    qtbot.keyClick(window, Qt.Key_Right)

    assert window._origin == QRect(11, 10, 30, 20)
    background = window._canvas.background_image()
    assert (background.width(), background.height()) == (30, 20)
    assert background.pixelColor(0, 0) == QColor("red")


def test_arrows_reach_the_window_through_the_canvas(qtbot, config):
    # The canvas (a QGraphicsView) must decline plain arrows instead of
    # scrolling, so the window-level adjustment still works when it has focus.
    window = _region_editor(qtbot, config)
    qtbot.keyClick(window._canvas, Qt.Key_Down)
    assert window._origin == QRect(10, 11, 30, 20)


def test_shift_arrow_moves_ten_native_pixels(qtbot, config):
    window = _region_editor(qtbot, config)
    qtbot.keyClick(window, Qt.Key_Right, Qt.ShiftModifier)
    assert window._origin == QRect(20, 10, 30, 20)


def test_alt_arrow_resizes_right_and_bottom_edge(qtbot, config):
    window = _region_editor(qtbot, config)
    qtbot.keyClick(window, Qt.Key_Right, Qt.AltModifier)  # width +1
    qtbot.keyClick(window, Qt.Key_Up, Qt.AltModifier)  # height -1
    assert window._origin == QRect(10, 10, 31, 19)
    background = window._canvas.background_image()
    assert (background.width(), background.height()) == (31, 19)


def test_arrow_step_is_native_not_logical_on_retina(qtbot, config):
    # At a 2x scale one press moves the crop by one *screenshot* pixel — half
    # a logical point — and the re-crop shifts by exactly that pixel.
    screenshot = _image(200, 100)
    screenshot.setPixelColor(21, 20, QColor("red"))  # native px of (10.5, 10)
    window = _region_editor(qtbot, config, native=(200, 100), screenshot=screenshot)

    qtbot.keyClick(window, Qt.Key_Right)

    assert window._selection.x() == 10.5
    assert window._selection.width() == 30  # both edges moved together
    background = window._canvas.background_image()
    assert background.pixelColor(0, 0) == QColor("red")
    assert (background.width(), background.height()) == (60, 40)


def test_move_clamps_to_desktop_edges(qtbot, config):
    window = _region_editor(qtbot, config)
    for _ in range(3):  # 30 px left of x=10
        qtbot.keyClick(window, Qt.Key_Left, Qt.ShiftModifier)
    assert window._origin == QRect(0, 10, 30, 20)
    for _ in range(9):
        qtbot.keyClick(window, Qt.Key_Right, Qt.ShiftModifier)
    assert window._origin == QRect(70, 10, 30, 20)  # right edge at 100


def test_resize_clamps_at_min_size_and_desktop_edge(qtbot, config):
    window = _region_editor(qtbot, config)
    for _ in range(5):
        qtbot.keyClick(window, Qt.Key_Left, Qt.AltModifier | Qt.ShiftModifier)
    assert window._selection.width() == 2  # _MIN_CROP
    for _ in range(10):
        qtbot.keyClick(window, Qt.Key_Right, Qt.AltModifier | Qt.ShiftModifier)
    assert window._selection.right() == 100  # desktop's right edge


def test_first_annotation_freezes_the_crop(qtbot, config):
    from PySide6.QtGui import QUndoCommand

    window = _region_editor(qtbot, config)
    window._canvas.undo_stack().push(QUndoCommand())  # an annotation landed
    qtbot.keyClick(window, Qt.Key_Right)
    assert window._origin == QRect(10, 10, 30, 20)  # unmoved


def test_arrows_do_nothing_without_region_context(qtbot, config):
    origin = QRect(10, 10, 30, 20)
    window = EditorWindow(_image(), config, origin)  # window/fullscreen shot
    qtbot.addWidget(window)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    qtbot.keyClick(window, Qt.Key_Right)
    assert window._origin == origin


def test_adjust_hint_shown_until_first_annotation(qtbot, config):
    from PySide6.QtGui import QUndoCommand

    from shotquill import i18n

    window = _region_editor(qtbot, config)
    # The editor picks the hint key per platform (⌥/⇧ on macOS, Alt/Shift
    # elsewhere); use the same selector so the test passes on either host.
    assert window.windowTitle() == i18n.t(i18n.adjust_hint_key())
    window._canvas.undo_stack().push(QUndoCommand())
    assert window.windowTitle() == i18n.t("title.annotate")


def test_annotation_does_not_clobber_other_statuses(qtbot, config, monkeypatch):
    # OCR finishing between open and the first annotation replaces the hint;
    # the annotation must then leave the OCR status alone.
    from PySide6.QtGui import QUndoCommand

    _patch_recognizer(monkeypatch, lines=["hi"])
    window = _region_editor(qtbot, config)
    window._ocr()
    _wait_ocr_done(qtbot, window)
    status = window.windowTitle()
    window._canvas.undo_stack().push(QUndoCommand())
    assert window.windowTitle() == status


def test_adjusted_crop_keeps_canvas_over_the_selection(qtbot, config):
    # After a nudge the editor must re-place itself so the (re-cropped) shot
    # still sits exactly over the on-screen selection.
    screenshot = _image(400, 300)
    geometry = QRect(0, 0, 400, 300)
    origin = QRect(120, 80, 200, 100)
    window = EditorWindow(
        screenshot.copy(origin), config, origin, RegionContext(screenshot, geometry)
    )
    qtbot.addWidget(window)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    window.show()
    qtbot.waitExposed(window)

    qtbot.keyClick(window, Qt.Key_Right)

    viewport = window._canvas.viewport()
    assert window._origin == QRect(121, 80, 200, 100)
    assert viewport.size() == window._origin.size()
    assert viewport.mapToGlobal(QPoint(0, 0)) == window._origin.topLeft()


# --- mouse crop-adjustment (edge drag -> full-screen adjust surface) ---------


def test_crop_adjustable_tracks_pristine(qtbot, config):
    from PySide6.QtGui import QUndoCommand

    window = _region_editor(qtbot, config)
    assert window.crop_adjustable() is True
    window._canvas.undo_stack().push(QUndoCommand())  # an annotation landed
    assert window.crop_adjustable() is False


def test_non_region_editor_registers_no_crop_host(qtbot, config):
    window = EditorWindow(_image(), config, QRect(10, 10, 30, 20))  # no RegionContext
    qtbot.addWidget(window)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    assert window._canvas._crop_host is None
    assert window.crop_adjustable() is False


def test_enter_crop_adjust_opens_overlay_seeded_with_current_crop(qtbot, config):
    from shotquill.ui.smart_overlay import CropAdjustOverlay

    window = _region_editor(qtbot, config)
    window.enter_crop_adjust((False, False, True, False))
    overlay = window._crop_overlay
    try:
        assert isinstance(overlay, CropAdjustOverlay)
        # Origin (10,10,30,20) seeds the surface; geometry origin is (0,0) so
        # overlay-local equals global here.
        assert overlay._sel == QRectF(10, 10, 30, 20)
    finally:
        overlay.close()


def test_crop_adjusted_recrops_from_the_full_screenshot(qtbot, config):
    # The apply path re-crops from the frozen screenshot via _apply_selection,
    # so the editor stays the single source of truth (the overlay's image arg is
    # ignored). Mark the pixel the widened crop's top-left should still land on.
    screenshot = _image(100, 50)
    screenshot.setPixelColor(10, 10, QColor("red"))
    window = _region_editor(qtbot, config, screenshot=screenshot)

    window._crop_adjusted(QImage(), QRect(10, 10, 50, 20))  # widened by the surface

    assert window._origin == QRect(10, 10, 50, 20)
    background = window._canvas.background_image()
    assert (background.width(), background.height()) == (50, 20)
    assert background.pixelColor(0, 0) == QColor("red")


def test_canvas_edge_press_enters_crop_adjust(qtbot, config):
    from PySide6.QtCore import QPoint as _QPoint

    window = _region_editor(qtbot, config)
    window.show()
    qtbot.waitExposed(window)
    calls = []
    window.enter_crop_adjust = lambda edges: calls.append(edges)  # don't spawn a real surface

    viewport = window._canvas.viewport()
    qtbot.mousePress(
        viewport, Qt.LeftButton, pos=_QPoint(viewport.width() - 1, viewport.height() // 2)
    )

    assert len(calls) == 1
    assert calls[0][2] is True  # the right edge was grabbed


def test_canvas_interior_press_does_not_enter_crop_adjust(qtbot, config):
    from PySide6.QtCore import QPoint as _QPoint

    window = _region_editor(qtbot, config)
    window.show()
    qtbot.waitExposed(window)
    calls = []
    window.enter_crop_adjust = lambda edges: calls.append(edges)

    viewport = window._canvas.viewport()
    qtbot.mousePress(
        viewport, Qt.LeftButton, pos=_QPoint(viewport.width() // 2, viewport.height() // 2)
    )

    assert calls == []  # a press in the middle is a normal (rubber-band) select


def test_canvas_edge_press_inert_after_first_annotation(qtbot, config):
    from PySide6.QtCore import QPoint as _QPoint
    from PySide6.QtGui import QUndoCommand

    window = _region_editor(qtbot, config)
    window.show()
    qtbot.waitExposed(window)
    window._canvas.undo_stack().push(QUndoCommand())  # crop is now frozen
    calls = []
    window.enter_crop_adjust = lambda edges: calls.append(edges)

    viewport = window._canvas.viewport()
    qtbot.mousePress(
        viewport, Qt.LeftButton, pos=_QPoint(viewport.width() - 1, viewport.height() // 2)
    )

    assert calls == []  # no longer adjustable, so the edge press does nothing


def test_spotlight_editor_disables_user_window_resize(qtbot, config, monkeypatch):
    # The frameless spotlight editor must not be user-resizable: on macOS the
    # window's resize edges otherwise hijack the crop-edge drag (and scale the
    # shot via fitInView) before it can reach the canvas. The AppKit call is a
    # no-op off macOS, so assert it is wired (not its effect).
    from shotquill.ui import macos_window

    calls = []
    monkeypatch.setattr(macos_window, "set_resizable", lambda w, r: calls.append(r))
    screenshot = _image(400, 300)
    geometry = QRect(0, 0, 400, 300)
    origin = QRect(120, 80, 200, 100)
    window = EditorWindow(
        screenshot.copy(origin), config, origin, RegionContext(screenshot, geometry)
    )
    qtbot.addWidget(window)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    window.show()
    qtbot.waitExposed(window)

    assert window._backdrop is not None  # spotlight (frameless) mode
    assert calls and calls[-1] is False  # the frameless window is made non-resizable


def test_framed_editor_keeps_default_resizability(qtbot, config, monkeypatch):
    # With the spotlight backdrop off the editor is a normal titled window; its
    # OS resize border is expected, so we must not disable it.
    from shotquill.ui import macos_window

    config.set_editor_backdrop(False)
    calls = []
    monkeypatch.setattr(macos_window, "set_resizable", lambda w, r: calls.append(r))
    window = EditorWindow(_image(), config, QRect(120, 80, 60, 40))
    qtbot.addWidget(window)
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    window.show()
    qtbot.waitExposed(window)

    assert window._backdrop is None  # framed mode
    assert calls == []


def test_editor_toolbar_style_follows_config(qtbot, config):
    from PySide6.QtWidgets import QToolBar

    config.set_toolbar_style("icon")
    window = _editor(qtbot, config)
    toolbar = window.findChild(QToolBar)
    assert toolbar.toolButtonStyle() == Qt.ToolButtonIconOnly


def test_width_spinbox_never_takes_keyboard_focus(qtbot, config):
    # The window's keyboard surface (arrow-key crop adjustment, Space/Enter
    # finish keys) must survive a click on the width spin box — a focusable
    # spin box would silently swallow the arrows for stepping the width.
    from PySide6.QtWidgets import QSpinBox

    window = _editor(qtbot, config)
    spinbox = window.findChild(QSpinBox)
    assert spinbox.focusPolicy() == Qt.NoFocus
    assert spinbox.lineEdit().focusPolicy() == Qt.NoFocus


def test_closing_editor_closes_the_crop_overlay(qtbot, config):
    # The crop-adjust surface is a separate top-level window held only by the
    # editor; closing the editor (e.g. Cmd-Q while it is up) must close it too
    # and drop the reference, so it can't outlive its host with dangling signals.
    screenshot = _image(100, 50)
    origin = QRect(10, 10, 30, 20)
    window = EditorWindow(
        screenshot.copy(origin), config, origin, RegionContext(screenshot, QRect(0, 0, 100, 50))
    )
    qtbot.addWidget(window)
    window.setAttribute(Qt.WA_DeleteOnClose, False)

    window.enter_crop_adjust((False, False, True, False))
    assert window._crop_overlay is not None

    window.close()
    assert window._crop_overlay is None  # closed + ref dropped, no dangling signals
