# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Builds the editor toolbar: tool picker, color, size, undo/redo, OCR, copy/save."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QActionGroup, QIcon, QKeySequence
from PySide6.QtWidgets import QColorDialog, QLabel, QSpinBox, QToolBar, QVBoxLayout, QWidget

from shotquill.config import DEFAULT_TOOLBAR_STYLE
from shotquill.i18n import t
from shotquill.ui.icons import (
    ICON_SIZE,
    ICON_SIZE_STANDALONE,
    ICON_STROKE_STANDALONE,
    toolbar_icon,
)
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

# Icon point size per style. "both" stacks a caption under the glyph, so a
# smaller icon keeps the two-row button compact and lines up over its label.
# "icon" has no caption — the glyph carries the whole button — so it gets the
# larger standalone size; at the stacked size it read visibly too small next to
# native toolbar icons (notably on macOS). "text" draws no icon, value unused.
_ICON_SIZES: dict[str, int] = {
    "both": ICON_SIZE,
    "icon": ICON_SIZE_STANDALONE,
    "text": ICON_SIZE,
}

# Pack the bar tighter than the platform default: with sixteen buttons the
# per-button padding plus the inter-item spacing and fat separators add up,
# and once the row no longer fits the shot's width Qt hides the overflow
# behind an extension chevron the user has to click open. Zeroing the
# toolbar's spacing/margins and slimming the separators keeps the whole row
# on one line for far narrower captures; stacked icon-over-label buttons read
# fine with a small caption (as macOS toolbars do). Only box-model and font
# properties are set, so the buttons keep their native hover/checked
# rendering.
_TIGHT_STYLE = (
    "QToolBar { spacing: 0px; padding: 0px; margin: 0px; }"
    "QToolBar::separator { width: 1px; margin: 0px 3px; }"
    "QToolButton { padding: 1px 0px; font-size: 11px; }"
    # The size control's caption (the only QLabel in the bar) sits on the same
    # line as the buttons' under-icon labels, so it matches their font size.
    "QLabel { font-size: 11px; }"
)
# Extra width, on top of the numeric value text, reserved for the spin box's
# up/down button column and inner margins. Capping the field to this keeps it
# from reserving the spin box's generous default width.
_SIZE_FIELD_PADDING = 22
_MAX_STROKE_WIDTH = 40
_MAX_FONT_SIZE = 160


class _NoCollapseToolBar(QToolBar):
    """A toolbar that never hides items behind the overflow chevron.

    QToolBar folds its trailing buttons into an extension popup once the row is
    narrower than its contents. Reporting the full size hint as the minimum keeps
    the host (the editor window's toolbar area) from ever shrinking the bar below
    that, so every button stays on the row. Used for the copy/save outputs so
    finishing a shot is always one visible click away, no matter how narrow the
    capture is (the tool row keeps Qt's normal folding — see create_toolbar).
    """

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()


def create_toolbar(
    canvas: AnnotationCanvas,
    on_copy: Callable[[], None],
    on_save: Callable[[], None],
    on_ocr: Callable[[], None] | None,
    on_pin: Callable[[], None],
    style: str = DEFAULT_TOOLBAR_STYLE,
    split_outputs: bool = False,
) -> QToolBar:
    toolbar = QToolBar()
    toolbar.setToolButtonStyle(_BUTTON_STYLES.get(style, _BUTTON_STYLES[DEFAULT_TOOLBAR_STYLE]))
    # Icon size follows the style (see _ICON_SIZES): icon-only buttons get a
    # larger glyph than the captioned "both" layout. Render every glyph at that
    # size and match the toolbar's icon size to it, so the buttons don't pad the
    # glyph back out (the platform default, 24+, would).
    icon_px = _ICON_SIZES.get(style, _ICON_SIZES[DEFAULT_TOOLBAR_STYLE])

    def sized_icon(name: str) -> QIcon:
        # The stroke scales with the glyph, so the larger icon-only glyph draws
        # heavy lines at the default width; thin it so its line weight stays
        # close to the smaller stacked icons. Other styles keep the default.
        if style == "icon":
            return toolbar_icon(name, icon_px, ICON_STROKE_STANDALONE)
        return toolbar_icon(name, icon_px)

    toolbar.setIconSize(QSize(icon_px, icon_px))
    toolbar.setStyleSheet(_TIGHT_STYLE)
    # Drop the drag handle (the dotted grip at the bar's leading edge): the
    # toolbar already auto-places itself in the corner nearest the pointer, so
    # the grip only ever stole horizontal room and risked an accidental
    # undock. Fixed-in-place removes the grip and reclaims that width.
    toolbar.setMovable(False)
    group = QActionGroup(toolbar)
    group.setExclusive(True)

    for key, tool, icon in _TOOLS:
        action = QAction(sized_icon(icon), t(key), toolbar)
        action.setCheckable(True)
        action.setChecked(tool == Tool.SELECT)
        action.triggered.connect(lambda _checked=False, bound=tool: canvas.set_tool(bound))
        group.addAction(action)
        toolbar.addAction(action)

    toolbar.addSeparator()

    # The macOS native colour panel can remain behind the frameless always-on-top
    # editor.  Never combine that panel with window modality: an invisible modal
    # panel blocks every editor key and makes the capture surface look frozen.
    # The parented Qt widget dialog has predictable transient-window behaviour,
    # and show() keeps it non-modal even if a window manager fails to front it.
    color_dialog = QColorDialog(canvas.color(), canvas)
    color_dialog.setOption(QColorDialog.DontUseNativeDialog)
    color_dialog.setWindowModality(Qt.NonModal)
    color_dialog.setWindowTitle(t("dialog.pick_color"))
    color_dialog.colorSelected.connect(canvas.set_color)

    def show_color_dialog() -> None:
        color_dialog.setCurrentColor(canvas.color())
        color_dialog.show()
        color_dialog.raise_()
        color_dialog.activateWindow()

    color_action = QAction(sized_icon("color"), t("toolbar.color"), toolbar)
    color_action.triggered.connect(show_color_dialog)
    toolbar.addAction(color_action)
    toolbar.color_dialog = color_dialog

    size = QSpinBox()
    size.setAlignment(Qt.AlignHCenter)
    # Frameless lets the field shrink to about the icons' height: a normal spin
    # box is taller than the icons, which would make the stacked control taller
    # than the buttons and push its caption off their label line.
    # Removing the box border slims it so the whole control matches a button's
    # height (pinned below) and the rows line up.
    size.setFrame(False)
    # The editor's keyboard surface lives on the canvas/window: arrows adjust
    # a region capture's crop, Space/Enter finish the shot. A focusable spin
    # box would keep those keys after a click — arrows would silently step the
    # active size instead of the crop — so it stays mouse-only (the up/down
    # buttons and the scroll wheel still adjust it). The inner line edit holds
    # its own focus policy and is the spin box's focus proxy, so clear both.
    size.setFocusPolicy(Qt.NoFocus)
    size.lineEdit().setFocusPolicy(Qt.NoFocus)

    def _set_active_size(value: int) -> None:
        if canvas.tool() == Tool.TEXT:
            canvas.set_font_size(value)
        else:
            canvas.set_width(value)

    size.valueChanged.connect(_set_active_size)

    # The field carries its dynamic name differently per style, so the control
    # always matches the buttons' row count:
    #   both  — number over a caption (two rows), like the icon-over-label
    #           buttons; the caption lands on their label line.
    #   icon  — number only, name via tooltip (one row), like the label-less
    #           icon buttons.
    #   text  — single-row labels, so the name goes inline as a prefix
    #           ("Width 4 px" / "Font size 32 pt"); a stacked caption would be
    #           clipped against the shorter single-row button height.
    # Unknown styles fall back to the two-row layout, matching the button-style
    # fallback above.
    width_label = t("toolbar.width").strip()
    font_size_label = t("toolbar.font_size").strip()

    def _cap_numeric_field(maximum: int, suffix: str) -> None:
        # Cap the field to its largest value plus the up/down button column, so
        # it doesn't reserve the spin box's (much wider) default size.
        value_width = size.fontMetrics().horizontalAdvance(f"{maximum}{suffix}")
        size.setMaximumWidth(value_width + _SIZE_FIELD_PADDING)

    size_control = QWidget()
    size_box = QVBoxLayout(size_control)
    size_box.setContentsMargins(0, 0, 0, 0)
    size_box.setSpacing(0)
    size_box.addWidget(size)
    size_caption: QLabel | None = None
    if style not in ("icon", "text"):
        size_caption = QLabel(width_label)
        size_caption.setAlignment(Qt.AlignHCenter)
        size_box.addWidget(size_caption)
        size_caption.setFixedHeight(size_caption.sizeHint().height())
    sample_button = toolbar.widgetForAction(toolbar.actions()[0])
    size_control.setFixedHeight(sample_button.sizeHint().height())
    toolbar.addWidget(size_control)

    def _sync_size_control(tool: Tool) -> None:
        is_text = tool == Tool.TEXT
        maximum = _MAX_FONT_SIZE if is_text else _MAX_STROKE_WIDTH
        value = canvas.font_size() if is_text else canvas.width()
        label = font_size_label if is_text else width_label
        suffix = " pt" if is_text else " px"

        signals_were_blocked = size.blockSignals(True)
        try:
            size.setRange(1, maximum)
            size.setValue(value)
            size.setSuffix(suffix)
            size.setToolTip(label)
            if style == "text":
                size.setPrefix(f"{label} ")
            else:
                _cap_numeric_field(maximum, suffix)
                if size_caption is not None:
                    size_caption.setText(label)
        finally:
            size.blockSignals(signals_were_blocked)

    canvas.tool_changed.connect(_sync_size_control)
    _sync_size_control(canvas.tool())

    # Exposed so tests (and any later sync) can reach the nested spin box. Keep
    # the old width_spin name as a compatibility alias while it becomes a
    # width-or-font-size control.
    toolbar.size_spin = size
    toolbar.width_spin = size

    toolbar.addSeparator()

    undo_action = canvas.undo_stack().createUndoAction(toolbar, t("toolbar.undo"))
    undo_action.setIcon(sized_icon("undo"))
    undo_action.setShortcut(QKeySequence.Undo)
    redo_action = canvas.undo_stack().createRedoAction(toolbar, t("toolbar.redo"))
    redo_action.setIcon(sized_icon("redo"))
    redo_action.setShortcut(QKeySequence.Redo)
    toolbar.addAction(undo_action)
    toolbar.addAction(redo_action)

    toolbar.addSeparator()

    # OCR is platform-gated: only shown when an on-device recognizer exists
    # (macOS Vision today). On Linux there's no backend, so the button is
    # omitted rather than offered as a guaranteed failure.
    if on_ocr is not None:
        ocr_action = QAction(sized_icon("ocr"), t("toolbar.ocr"), toolbar)
        ocr_action.setToolTip(t("toolbar.ocr_tip"))
        ocr_action.triggered.connect(on_ocr)
        toolbar.addAction(ocr_action)

    pin_action = QAction(sized_icon("pin"), t("toolbar.pin"), toolbar)
    pin_action.setToolTip(t("toolbar.pin_tip"))
    pin_action.triggered.connect(on_pin)
    toolbar.addAction(pin_action)

    copy_action = QAction(sized_icon("copy"), t("toolbar.copy"), toolbar)
    copy_action.setShortcut(QKeySequence.Copy)
    copy_action.setToolTip(t("toolbar.copy_tip"))
    copy_action.triggered.connect(on_copy)

    save_action = QAction(sized_icon("save"), t("toolbar.save"), toolbar)
    save_action.setShortcut(QKeySequence.Save)
    save_action.setToolTip(t("toolbar.save_tip"))
    save_action.triggered.connect(on_save)

    # Copy/save are the shot's finish actions, so they must never fold away. When
    # the host can constrain the bar's width, split them into a fixed
    # no-collapse section that stays visible while the annotation section folds.
    # The host decides whether the two sections share a continuous row.
    if split_outputs:
        outputs = _NoCollapseToolBar()
        outputs.setToolButtonStyle(toolbar.toolButtonStyle())
        outputs.setIconSize(toolbar.iconSize())
        outputs.setStyleSheet(_TIGHT_STYLE)
        outputs.setMovable(False)
        outputs.addAction(copy_action)
        outputs.addAction(save_action)
        toolbar.outputs_toolbar = outputs
    else:
        toolbar.addAction(copy_action)
        toolbar.addAction(save_action)
        toolbar.outputs_toolbar = None

    # Exposed so EditorWindow can keep these tooltips in sync with the
    # configurable finish keys (see EditorWindow._refresh_finish_tips).
    toolbar.copy_action = copy_action
    toolbar.save_action = save_action

    return toolbar
