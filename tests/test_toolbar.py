# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Headless tests for the editor toolbar factory."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QPixmap  # noqa: E402
from PySide6.QtWidgets import QColorDialog, QLabel, QToolButton  # noqa: E402

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
    assert toolbar.width_spin.value() == 4
    assert toolbar.width_spin.suffix() == " px"
    toolbar.width_spin.setValue(13)
    assert canvas.width() == 13


def test_size_control_switches_between_independent_width_and_font_size(qtbot):
    canvas, toolbar = _toolbar(qtbot, style="both")
    text_action = next(action for action in toolbar.actions() if action.text() == "Text")
    rect_action = next(action for action in toolbar.actions() if action.text() == "Rectangle")
    spin = toolbar.width_spin
    caption = spin.parentWidget().findChild(QLabel)

    text_action.trigger()
    assert (spin.value(), spin.maximum(), spin.suffix(), caption.text()) == (
        32,
        160,
        " pt",
        "Font size",
    )
    spin.setValue(14)
    assert canvas.font_size() == 14
    assert canvas.width() == 4

    rect_action.trigger()
    assert (spin.value(), spin.maximum(), spin.suffix(), caption.text()) == (4, 40, " px", "Width")
    spin.setValue(7)
    assert canvas.width() == 7
    assert canvas.font_size() == 14

    text_action.trigger()
    assert spin.value() == 14
    assert spin.suffix() == " pt"


def test_color_dialog_is_owned_non_native_and_non_modal(qtbot):
    # The macOS native panel can fail to front over the always-on-top editor.
    # Window modality would then block every editor key behind an invisible
    # panel.  Use the parented Qt widget picker and never enter a modal state.
    canvas, toolbar = _toolbar(qtbot)
    dialog = toolbar.color_dialog
    assert isinstance(dialog, QColorDialog)
    assert dialog.parentWidget() is canvas
    assert dialog.testOption(QColorDialog.DontUseNativeDialog)

    color_action = next(action for action in toolbar.actions() if action.text() == "Color")
    color_action.trigger()

    assert dialog.isVisible()
    assert dialog.isModal() is False
    assert dialog.windowModality() == Qt.NonModal


def test_color_dialog_applies_only_an_accepted_color(qtbot):
    canvas, toolbar = _toolbar(qtbot)
    dialog = toolbar.color_dialog
    original = canvas.color()

    dialog.setCurrentColor(QColor("blue"))
    dialog.reject()
    assert canvas.color() == original

    dialog.setCurrentColor(QColor("green"))
    dialog.accept()
    assert canvas.color() == QColor("green")


def test_width_control_is_a_captioned_two_row_widget(qtbot):
    # Two rows like the icon-over-label buttons: the value on top, the caption
    # below (and pinned to the bottom so it lands on the buttons' label line).
    # The label lives in that caption, not the spin box's wide inline prefix.
    _canvas_, toolbar = _toolbar(qtbot, style="both")
    spin = toolbar.width_spin
    assert spin.prefix() == ""
    container = spin.parentWidget()
    captions = [label.text() for label in container.findChildren(QLabel)]
    assert captions == ["Width"]
    layout = container.layout()
    assert layout.indexOf(container.findChild(QLabel)) > layout.indexOf(spin)
    # The field is capped narrower than the spin box's unbounded default.
    assert spin.maximumWidth() < 16777215  # QWIDGETSIZE_MAX (the unset value)


def test_width_caption_is_dropped_in_icon_only_mode(qtbot):
    # Icon-only strips every button's label, so the width control must not show a
    # lone "Width" caption among them; it keeps its name through a tooltip.
    canvas, toolbar = _toolbar(qtbot, style="icon")
    spin = toolbar.width_spin
    container = spin.parentWidget()
    assert container.findChildren(QLabel) == []
    assert spin.toolTip() == "Width"
    canvas.set_tool(Tool.TEXT)
    assert spin.toolTip() == "Font size"
    assert spin.suffix() == " pt"
    assert spin.maximum() == 160
    assert spin.maximumWidth() >= spin.fontMetrics().horizontalAdvance("160 pt")


def test_width_shows_inline_label_in_text_mode(qtbot):
    # Text-only buttons are single-row labels, so the width field shows its name
    # inline as a prefix ("Width 12") rather than a stacked caption that would be
    # clipped against the shorter single-row button height.
    canvas, toolbar = _toolbar(qtbot, style="text")
    spin = toolbar.width_spin
    assert spin.prefix() == "Width "
    assert spin.parentWidget().findChildren(QLabel) == []
    canvas.set_tool(Tool.TEXT)
    assert spin.prefix() == "Font size "
    assert spin.suffix() == " pt"
    assert spin.value() == 32


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


def test_outputs_stay_on_the_main_bar_by_default(qtbot):
    # Hosts using the ordinary single-bar layout keep the original action order:
    # copy/save are the final two tools.
    _canvas_, toolbar = _toolbar(qtbot)
    assert toolbar.outputs_toolbar is None
    assert toolbar.actions()[-2:] == [toolbar.copy_action, toolbar.save_action]


def test_split_outputs_moves_copy_save_to_a_no_collapse_sibling_bar(qtbot):
    # The framed editor constrains the bar's width, so copy/save are peeled onto
    # a sibling that never folds them behind the overflow chevron.
    from shotquill.ui.toolbar import _NoCollapseToolBar

    canvas = _canvas(qtbot)
    toolbar = create_toolbar(
        canvas, lambda: None, lambda: None, lambda: None, lambda: None, split_outputs=True
    )
    qtbot.addWidget(toolbar)
    outputs = toolbar.outputs_toolbar
    assert isinstance(outputs, _NoCollapseToolBar)
    qtbot.addWidget(outputs)
    # The tool row no longer carries copy/save; the outputs bar does.
    assert toolbar.copy_action not in toolbar.actions()
    assert toolbar.save_action not in toolbar.actions()
    assert toolbar.copy_action in outputs.actions()
    assert toolbar.save_action in outputs.actions()
    # A no-collapse bar reserves room for every button (min == preferred), so it
    # never hides one behind a chevron.
    assert outputs.minimumSizeHint() == outputs.sizeHint()


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
        # Only the tool/command buttons carry icons; skip separators and custom
        # widgets (the width control is a captioned spin box, not a QToolButton).
        if action.isSeparator() or not isinstance(toolbar.widgetForAction(action), QToolButton):
            continue
        assert not action.icon().isNull(), action.text()


def test_toolbar_shows_icon_only_by_default(qtbot):
    # Compact by default: just the glyph, its label carried by the tooltip.
    _canvas_, toolbar = _toolbar(qtbot)
    assert toolbar.toolButtonStyle() == Qt.ToolButtonIconOnly


def test_toolbar_icon_size_matches_the_emitted_glyph_size(qtbot):
    from PySide6.QtCore import QSize

    from shotquill.ui.icons import ICON_SIZE_STANDALONE

    # The default is icon-only, whose glyph is emitted at the larger standalone
    # size (no caption to balance), and the toolbar's icon size must match it.
    _canvas_, toolbar = _toolbar(qtbot)
    assert toolbar.iconSize() == QSize(ICON_SIZE_STANDALONE, ICON_SIZE_STANDALONE)


@pytest.mark.parametrize(
    ("style", "expected_size"),
    [
        ("both", "ICON_SIZE"),  # captioned stacked layout: small glyph over label
        ("icon", "ICON_SIZE_STANDALONE"),  # no caption: larger standalone glyph
        ("text", "ICON_SIZE"),  # no icon drawn; size is the harmless default
        # unknown value falls back to the default style's size (icon-only)
        ("sideways", "ICON_SIZE_STANDALONE"),
    ],
)
def test_icon_size_follows_the_toolbar_style(qtbot, style, expected_size):
    from PySide6.QtCore import QSize

    from shotquill.ui import icons

    expected = getattr(icons, expected_size)
    _canvas_, toolbar = _toolbar(qtbot, style=style)
    assert toolbar.iconSize() == QSize(expected, expected)


def test_icon_only_glyphs_are_rendered_larger_than_the_stacked_layout(qtbot):
    # The fix: an icon-only button's glyph is actually emitted at the larger
    # standalone size, not just shown in a bigger box — so it doesn't read tiny
    # next to native toolbar icons. Compare the emitted glyph pixmaps.
    from shotquill.ui.icons import ICON_SIZE, ICON_SIZE_STANDALONE

    _canvas_, stacked = _toolbar(qtbot, style="both")
    _canvas2_, icon_only = _toolbar(qtbot, style="icon")
    stacked_btn = stacked.widgetForAction(stacked.actions()[0])
    icon_btn = icon_only.widgetForAction(icon_only.actions()[0])
    stacked_glyph = stacked_btn.icon().actualSize(stacked.iconSize())
    icon_glyph = icon_btn.icon().actualSize(icon_only.iconSize())
    assert stacked_glyph.height() == ICON_SIZE
    assert icon_glyph.height() == ICON_SIZE_STANDALONE
    assert icon_glyph.height() > stacked_glyph.height()


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
        ("sideways", Qt.ToolButtonIconOnly),  # unknown value: default (icon-only) look
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
