# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Builds the editor toolbar: tool picker, color, width, undo/redo, copy/save."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QColorDialog, QSpinBox, QToolBar

from shotquill.ui.tools import Tool

if TYPE_CHECKING:
    from shotquill.ui.canvas import AnnotationCanvas

_TOOLS: list[tuple[str, Tool]] = [
    ("选择", Tool.SELECT),
    ("矩形", Tool.RECT),
    ("圆", Tool.ELLIPSE),
    ("箭头", Tool.ARROW),
    ("直线", Tool.LINE),
    ("画笔", Tool.PEN),
    ("荧光笔", Tool.HIGHLIGHTER),
    ("文字", Tool.TEXT),
]


def _pick_color(canvas: AnnotationCanvas) -> None:
    color = QColorDialog.getColor(canvas.color(), None, "选择颜色")
    if color.isValid():
        canvas.set_color(color)


def create_toolbar(
    canvas: AnnotationCanvas,
    on_copy: Callable[[], None],
    on_save: Callable[[], None],
) -> QToolBar:
    toolbar = QToolBar("工具")
    group = QActionGroup(toolbar)
    group.setExclusive(True)

    for label, tool in _TOOLS:
        action = QAction(label, toolbar)
        action.setCheckable(True)
        action.setChecked(tool == Tool.SELECT)
        action.triggered.connect(lambda _checked=False, t=tool: canvas.set_tool(t))
        group.addAction(action)
        toolbar.addAction(action)

    toolbar.addSeparator()

    color_action = QAction("颜色", toolbar)
    color_action.triggered.connect(lambda: _pick_color(canvas))
    toolbar.addAction(color_action)

    width = QSpinBox()
    width.setRange(1, 40)
    width.setValue(canvas.width())
    width.setPrefix("粗细 ")
    width.valueChanged.connect(canvas.set_width)
    toolbar.addWidget(width)

    toolbar.addSeparator()

    undo_action = canvas.undo_stack().createUndoAction(toolbar, "撤销")
    undo_action.setShortcut(QKeySequence.Undo)
    redo_action = canvas.undo_stack().createRedoAction(toolbar, "重做")
    redo_action.setShortcut(QKeySequence.Redo)
    toolbar.addAction(undo_action)
    toolbar.addAction(redo_action)

    toolbar.addSeparator()

    copy_action = QAction("复制", toolbar)
    copy_action.setShortcut(QKeySequence.Copy)
    copy_action.triggered.connect(on_copy)
    toolbar.addAction(copy_action)

    save_action = QAction("保存", toolbar)
    save_action.setShortcut(QKeySequence.Save)
    save_action.triggered.connect(on_save)
    toolbar.addAction(save_action)

    return toolbar
