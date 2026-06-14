# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Headless tests for the vector-drawn toolbar icons."""

import pytest

pytest.importorskip("PySide6")

from shotquill.ui.icons import _SCALE, ICON_NAMES, ICON_SIZE, toolbar_icon  # noqa: E402


def test_glyphs_are_emitted_at_the_configured_size(qtbot):
    from PySide6.QtCore import QSize

    physical = QSize(ICON_SIZE * _SCALE, ICON_SIZE * _SCALE)
    assert toolbar_icon("rect").availableSizes() == [physical]


def test_every_glyph_renders_visible_pixels(qtbot):
    for name in ICON_NAMES:
        icon = toolbar_icon(name)
        assert not icon.isNull(), name
        image = icon.pixmap(24, 24).toImage()
        assert any(
            image.pixelColor(x, y).alpha() > 0
            for x in range(image.width())
            for y in range(image.height())
        ), f"{name} rendered fully transparent"


def test_glyphs_are_distinct(qtbot):
    # A copy-paste slip in the glyph table would silently give two buttons the
    # same picture; compare rendered bytes to catch it.
    rendered = {}
    for name in ICON_NAMES:
        image = toolbar_icon(name).pixmap(24, 24).toImage()
        rendered[bytes(image.constBits())] = name
    assert len(rendered) == len(ICON_NAMES)


def test_unknown_glyph_name_raises():
    with pytest.raises(KeyError):
        toolbar_icon("nonexistent")


def test_thinner_stroke_paints_fewer_pixels(qtbot):
    # The stroke argument must actually reach the pen: a thinner stroke covers
    # less of the glyph, so it paints strictly fewer opaque pixels at one size.
    def opaque(stroke):
        image = toolbar_icon("rect", ICON_SIZE, stroke).pixmap(ICON_SIZE, ICON_SIZE).toImage()
        return sum(
            image.pixelColor(x, y).alpha() > 0
            for x in range(image.width())
            for y in range(image.height())
        )

    assert opaque(1.5) < opaque(2.5)
