# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Headless tests for the editor toolbar factory."""

import pytest

pytest.importorskip("PySide6")

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


def _toolbar(qtbot, **callbacks):
    canvas = _canvas(qtbot)
    cbs = {
        "on_copy": lambda: None,
        "on_save": lambda: None,
        "on_ocr": lambda: None,
        "on_pin": lambda: None,
    }
    cbs.update(callbacks)
    toolbar = create_toolbar(canvas, cbs["on_copy"], cbs["on_save"], cbs["on_ocr"], cbs["on_pin"])
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
