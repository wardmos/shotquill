# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Builds the editor toolbar: tool picker, color, width, undo/redo, OCR, copy/save."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QColorDialog, QSpinBox, QToolBar

from shotquill.config import DEFAULT_TOOLBAR_STYLE
from shotquill.i18n import t
from shotquill.ui.icons import ICON_SIZE, toolbar_icon
from shotquill.ui.tools import Tool

if TYPE_CHECKING:
    from shotquill.ui.canvas import AnnotationCanvas

# Standard shortcuts the toolbar binds below (Esc is bound by EditorWindow on
# top of these). The Settings dialog derives its finish-key denylist from this
# tuple, so adding a setShortcut call here means adding its key to the tuple.
RESERVED_SHORTCUTS: tuple[QKeySequence.StandardKey, ...] = (
    QKeySequence.Copy,
    QKeySequence.Save,
    QKeySequence.Undo,
    QKeySequence.Redo,
)

# (i18n key, tool, icon name) — the icon names index shotquill.ui.icons.
_TOOLS: list[tuple[str, Tool, str]] = [
    ("tool.select", Tool.SELECT, "select"),
    ("tool.rect", Tool.RECT, "rect"),
    ("tool.ellipse", Tool.ELLIPSE, "ellipse"),
    ("tool.arrow", Tool.ARROW, "arrow"),
    ("tool.line", Tool.LINE, "line"),
    ("tool.pen", Tool.PEN, "pen"),
    ("tool.highlighter", Tool.HIGHLIGHTER, "highlighter"),
    ("tool.mosaic", Tool.MOSAIC, "mosaic"),
    ("tool.text", Tool.TEXT, "text"),
]

# Configured style string → how QToolBar lays out each button. "both" stacks
# the label under its icon, so across the bar the icons share one row and the
# labels another — same-size glyphs centred over their text line up cleanly.
# Icon-only keeps the label readable through the tooltip (a QAction's tooltip
# defaults to its text, and the actions with bespoke tooltips already mention
# their function).
_BUTTON_STYLES: dict[str, Qt.ToolButtonStyle] = {
    "both": Qt.ToolButtonTextUnderIcon,
    "icon": Qt.ToolButtonIconOnly,
    "text": Qt.ToolButtonTextOnly,
}

# Pack the buttons tighter than the platform default: with sixteen buttons in
# the bar the per-button horizontal padding adds up, and stacked icon-over-
# label buttons read fine with a small caption (as macOS toolbars do). Only
# box-model and font properties are set, so the buttons keep their native
# hover/checked rendering.
_TIGHT_BUTTONS = "QToolButton { padding: 1px 0px; font-size: 11px; }"


def _pick_color(canvas: AnnotationCanvas) -> None:
    color = QColorDialog.getColor(canvas.color(), None, t("dialog.pick_color"))
    if color.isValid():
        canvas.set_color(color)


def create_toolbar(
    canvas: AnnotationCanvas,
    on_copy: Callable[[], None],
    on_save: Callable[[], None],
    on_ocr: Callable[[], None] | None,
    on_pin: Callable[[], None],
    style: str = DEFAULT_TOOLBAR_STYLE,
) -> QToolBar:
    toolbar = QToolBar()
    toolbar.setToolButtonStyle(_BUTTON_STYLES.get(style, _BUTTON_STYLES[DEFAULT_TOOLBAR_STYLE]))
    # Match the glyphs' emitted size; the platform default (24+) would pad
    # every button back out around the smaller icons.
    toolbar.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
    toolbar.setStyleSheet(_TIGHT_BUTTONS)
    group = QActionGroup(toolbar)
    group.setExclusive(True)

    for key, tool, icon in _TOOLS:
        action = QAction(toolbar_icon(icon), t(key), toolbar)
        action.setCheckable(True)
        action.setChecked(tool == Tool.SELECT)
        action.triggered.connect(lambda _checked=False, bound=tool: canvas.set_tool(bound))
        group.addAction(action)
        toolbar.addAction(action)

    toolbar.addSeparator()

    color_action = QAction(toolbar_icon("color"), t("toolbar.color"), toolbar)
    color_action.triggered.connect(lambda: _pick_color(canvas))
    toolbar.addAction(color_action)

    width = QSpinBox()
    width.setRange(1, 40)
    width.setValue(canvas.width())
    width.setPrefix(t("toolbar.width"))
    # The editor's keyboard surface lives on the canvas/window: arrows adjust
    # a region capture's crop, Space/Enter finish the shot. A focusable spin
    # box would keep those keys after a click — arrows would silently step the
    # stroke width instead of the crop — so it stays mouse-only (the up/down
    # buttons and the scroll wheel still adjust it). The inner line edit holds
    # its own focus policy and is the spin box's focus proxy, so clear both.
    width.setFocusPolicy(Qt.NoFocus)
    width.lineEdit().setFocusPolicy(Qt.NoFocus)
    width.valueChanged.connect(canvas.set_width)
    toolbar.addWidget(width)

    toolbar.addSeparator()

    undo_action = canvas.undo_stack().createUndoAction(toolbar, t("toolbar.undo"))
    undo_action.setIcon(toolbar_icon("undo"))
    undo_action.setShortcut(QKeySequence.Undo)
    redo_action = canvas.undo_stack().createRedoAction(toolbar, t("toolbar.redo"))
    redo_action.setIcon(toolbar_icon("redo"))
    redo_action.setShortcut(QKeySequence.Redo)
    toolbar.addAction(undo_action)
    toolbar.addAction(redo_action)

    toolbar.addSeparator()

    # OCR is platform-gated: only shown when an on-device recognizer exists
    # (macOS Vision today). On Linux there's no backend, so the button is
    # omitted rather than offered as a guaranteed failure.
    if on_ocr is not None:
        ocr_action = QAction(toolbar_icon("ocr"), t("toolbar.ocr"), toolbar)
        ocr_action.setToolTip(t("toolbar.ocr_tip"))
        ocr_action.triggered.connect(on_ocr)
        toolbar.addAction(ocr_action)

    pin_action = QAction(toolbar_icon("pin"), t("toolbar.pin"), toolbar)
    pin_action.setToolTip(t("toolbar.pin_tip"))
    pin_action.triggered.connect(on_pin)
    toolbar.addAction(pin_action)

    copy_action = QAction(toolbar_icon("copy"), t("toolbar.copy"), toolbar)
    copy_action.setShortcut(QKeySequence.Copy)
    copy_action.setToolTip(t("toolbar.copy_tip"))
    copy_action.triggered.connect(on_copy)
    toolbar.addAction(copy_action)

    save_action = QAction(toolbar_icon("save"), t("toolbar.save"), toolbar)
    save_action.setShortcut(QKeySequence.Save)
    save_action.setToolTip(t("toolbar.save_tip"))
    save_action.triggered.connect(on_save)
    toolbar.addAction(save_action)

    # Exposed so EditorWindow can keep these tooltips in sync with the
    # configurable finish keys (see EditorWindow._refresh_finish_tips).
    toolbar.copy_action = copy_action
    toolbar.save_action = save_action

    return toolbar
