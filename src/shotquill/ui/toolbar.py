# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Builds the editor toolbar: tool picker, color, width, undo/redo, OCR, copy/save."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QColorDialog, QSpinBox, QToolBar

from shotquill.i18n import t
from shotquill.ui.tools import Tool

if TYPE_CHECKING:
    from shotquill.ui.canvas import AnnotationCanvas

_TOOLS: list[tuple[str, Tool]] = [
    ("tool.select", Tool.SELECT),
    ("tool.rect", Tool.RECT),
    ("tool.ellipse", Tool.ELLIPSE),
    ("tool.arrow", Tool.ARROW),
    ("tool.line", Tool.LINE),
    ("tool.pen", Tool.PEN),
    ("tool.highlighter", Tool.HIGHLIGHTER),
    ("tool.mosaic", Tool.MOSAIC),
    ("tool.text", Tool.TEXT),
]


def _pick_color(canvas: AnnotationCanvas) -> None:
    color = QColorDialog.getColor(canvas.color(), None, t("dialog.pick_color"))
    if color.isValid():
        canvas.set_color(color)


def create_toolbar(
    canvas: AnnotationCanvas,
    on_copy: Callable[[], None],
    on_save: Callable[[], None],
    on_ocr: Callable[[], None],
    on_pin: Callable[[], None],
) -> QToolBar:
    toolbar = QToolBar()
    group = QActionGroup(toolbar)
    group.setExclusive(True)

    for key, tool in _TOOLS:
        action = QAction(t(key), toolbar)
        action.setCheckable(True)
        action.setChecked(tool == Tool.SELECT)
        action.triggered.connect(lambda _checked=False, bound=tool: canvas.set_tool(bound))
        group.addAction(action)
        toolbar.addAction(action)

    toolbar.addSeparator()

    color_action = QAction(t("toolbar.color"), toolbar)
    color_action.triggered.connect(lambda: _pick_color(canvas))
    toolbar.addAction(color_action)

    width = QSpinBox()
    width.setRange(1, 40)
    width.setValue(canvas.width())
    width.setPrefix(t("toolbar.width"))
    width.valueChanged.connect(canvas.set_width)
    toolbar.addWidget(width)

    toolbar.addSeparator()

    undo_action = canvas.undo_stack().createUndoAction(toolbar, t("toolbar.undo"))
    undo_action.setShortcut(QKeySequence.Undo)
    redo_action = canvas.undo_stack().createRedoAction(toolbar, t("toolbar.redo"))
    redo_action.setShortcut(QKeySequence.Redo)
    toolbar.addAction(undo_action)
    toolbar.addAction(redo_action)

    toolbar.addSeparator()

    ocr_action = QAction(t("toolbar.ocr"), toolbar)
    ocr_action.setToolTip(t("toolbar.ocr_tip"))
    ocr_action.triggered.connect(on_ocr)
    toolbar.addAction(ocr_action)

    pin_action = QAction(t("toolbar.pin"), toolbar)
    pin_action.setToolTip(t("toolbar.pin_tip"))
    pin_action.triggered.connect(on_pin)
    toolbar.addAction(pin_action)

    # The editor looks these two up by object name to keep their tooltips in
    # sync with the configurable finish keys (see EditorWindow._refresh_finish_tips).
    copy_action = QAction(t("toolbar.copy"), toolbar)
    copy_action.setObjectName("copy_action")
    copy_action.setShortcut(QKeySequence.Copy)
    copy_action.setToolTip(t("toolbar.copy_tip"))
    copy_action.triggered.connect(on_copy)
    toolbar.addAction(copy_action)

    save_action = QAction(t("toolbar.save"), toolbar)
    save_action.setObjectName("save_action")
    save_action.setShortcut(QKeySequence.Save)
    save_action.setToolTip(t("toolbar.save_tip"))
    save_action.triggered.connect(on_save)
    toolbar.addAction(save_action)

    return toolbar
