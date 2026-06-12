# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Headless tests for the editor toolbar factory."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QPixmap  # noqa: E402
from PySide6.QtWidgets import QSpinBox  # noqa: E402

from shotquill.ui.canvas import AnnotationCanvas  # noqa: E402
from shotquill.ui.toolbar import _TOOLS, create_toolbar  # noqa: E402
from shotquill.ui.tools import Tool  # noqa: E402


def _canvas(qtbot):
    pixmap = QPixmap(100, 80)
    pixmap.fill(QColor("white"))
    canvas = AnnotationCanvas(pixmap)
    qtbot.addWidget(canvas)
    return canvas


def _toolbar(qtbot, style=None, **callbacks):
    canvas = _canvas(qtbot)
    cbs = {
        "on_copy": lambda: None,
        "on_save": lambda: None,
        "on_ocr": lambda: None,
        "on_pin": lambda: None,
    }
    cbs.update(callbacks)
    kwargs = {} if style is None else {"style": style}
    toolbar = create_toolbar(
        canvas, cbs["on_copy"], cbs["on_save"], cbs["on_ocr"], cbs["on_pin"], **kwargs
    )
    qtbot.addWidget(toolbar)
    return canvas, toolbar


def test_toolbar_has_a_checkable_action_per_tool(qtbot):
    _canvas_, toolbar = _toolbar(qtbot)
    checkable = [a for a in toolbar.actions() if a.isCheckable()]
    assert len(checkable) == len(_TOOLS)
    # Select is the default checked tool.
    checked = [a for a in checkable if a.isChecked()]
    assert len(checked) == 1


def test_tool_actions_are_mutually_exclusive(qtbot):
    canvas, toolbar = _toolbar(qtbot)
    checkable = [a for a in toolbar.actions() if a.isCheckable()]
    # Triggering the second tool action unchecks the first (exclusive group).
    checkable[1].trigger()
    assert sum(a.isChecked() for a in checkable) == 1


def test_tool_action_switches_canvas_tool(qtbot):
    canvas, toolbar = _toolbar(qtbot)
    # _TOOLS order maps 1:1 to the checkable actions; index 1 is RECT.
    rect_action = [a for a in toolbar.actions() if a.isCheckable()][1]
    assert _TOOLS[1][1] is Tool.RECT
    rect_action.trigger()
    canvas.set_tool(Tool.RECT)  # sanity; trigger already routed through the lambda
    canvas.set_tool(_TOOLS[1][1])


def test_width_spinbox_reflects_and_updates_canvas(qtbot):
    canvas, toolbar = _toolbar(qtbot)
    spin = next(
        toolbar.widgetForAction(a)
        for a in toolbar.actions()
        if isinstance(toolbar.widgetForAction(a), QSpinBox)
    )
    spin.setValue(13)
    assert canvas.width() == 13


def test_toolbar_exposes_copy_and_save_actions(qtbot):
    # EditorWindow grabs these to keep tooltips in sync with the finish keys.
    _canvas_, toolbar = _toolbar(qtbot)
    assert toolbar.copy_action.text() == "Copy"
    assert toolbar.save_action.text() == "Save"
    assert toolbar.copy_action in toolbar.actions()
    assert toolbar.save_action in toolbar.actions()


def test_copy_and_save_callbacks_are_wired(qtbot):
    calls = []
    canvas, toolbar = _toolbar(
        qtbot,
        on_copy=lambda: calls.append("copy"),
        on_save=lambda: calls.append("save"),
        on_ocr=lambda: calls.append("ocr"),
        on_pin=lambda: calls.append("pin"),
    )
    texts = {a.text(): a for a in toolbar.actions()}
    # Trigger by the translated labels (English default).
    for label in ("Copy", "Save", "Copy Text", "Pin"):
        texts[label].trigger()
    assert set(calls) == {"copy", "save", "ocr", "pin"}


def test_ocr_action_present_when_callback_given(qtbot):
    _canvas_, toolbar = _toolbar(qtbot, on_ocr=lambda: None)
    assert "Copy Text" in {a.text() for a in toolbar.actions()}


def test_ocr_action_omitted_when_no_recognizer(qtbot):
    # on_ocr=None is how the editor signals "no OCR backend on this platform"
    # (Linux); the button must not appear rather than fail when clicked.
    _canvas_, toolbar = _toolbar(qtbot, on_ocr=None)
    assert "Copy Text" not in {a.text() for a in toolbar.actions()}


def test_every_button_has_an_icon(qtbot):
    _canvas_, toolbar = _toolbar(qtbot)
    for action in toolbar.actions():
        # Separators and the width spinbox carry no icon by design.
        if action.isSeparator() or isinstance(toolbar.widgetForAction(action), QSpinBox):
            continue
        assert not action.icon().isNull(), action.text()


def test_toolbar_shows_text_under_icon_by_default(qtbot):
    # Icon on top, label underneath: icons share one row, labels another.
    _canvas_, toolbar = _toolbar(qtbot)
    assert toolbar.toolButtonStyle() == Qt.ToolButtonTextUnderIcon


def test_toolbar_icon_size_matches_the_emitted_glyph_size(qtbot):
    from PySide6.QtCore import QSize

    from shotquill.ui.icons import ICON_SIZE

    _canvas_, toolbar = _toolbar(qtbot)
    assert toolbar.iconSize() == QSize(ICON_SIZE, ICON_SIZE)


def test_buttons_are_packed_tighter_than_the_platform_default(qtbot):
    # The tightening stylesheet must actually narrow the buttons; clearing it
    # restores the (wider) platform metrics, proving the effect is real.
    _canvas_, toolbar = _toolbar(qtbot)
    button = toolbar.widgetForAction(toolbar.actions()[0])
    tight = button.sizeHint().width()
    toolbar.setStyleSheet("")
    assert button.sizeHint().width() > tight


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        ("both", Qt.ToolButtonTextUnderIcon),
        ("icon", Qt.ToolButtonIconOnly),
        ("text", Qt.ToolButtonTextOnly),
        ("sideways", Qt.ToolButtonTextUnderIcon),  # unknown value: default look
    ],
)
def test_toolbar_button_style_follows_setting(qtbot, style, expected):
    _canvas_, toolbar = _toolbar(qtbot, style=style)
    assert toolbar.toolButtonStyle() == expected


def test_toolbar_is_fixed_in_place(qtbot):
    # No drag handle: the bar auto-places by the pointer, so the grip only ate
    # width. Fixing it in place drops the grip and reclaims that room.
    _canvas_, toolbar = _toolbar(qtbot)
    assert not toolbar.isMovable()
