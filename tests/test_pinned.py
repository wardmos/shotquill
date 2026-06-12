# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Headless Qt tests for the pinned-screenshot window.

Verifies window flags, sizing (including down-scaling oversized shots), and the
Esc/double-click dismissal path. Runs under ``QT_QPA_PLATFORM=offscreen``.
"""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from shotquill.i18n import t  # noqa: E402
from shotquill.ui.pinned import PinnedWindow  # noqa: E402


def _image(width, height, color="white"):
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    return image


def test_pinned_window_is_frameless_and_on_top(qtbot):
    window = PinnedWindow(_image(100, 80))
    qtbot.addWidget(window)
    flags = window.windowFlags()
    assert flags & Qt.FramelessWindowHint
    assert flags & Qt.WindowStaysOnTopHint


def test_pinned_window_sizes_to_logical_image(qtbot):
    dpr = QGuiApplication.primaryScreen().devicePixelRatio()
    window = PinnedWindow(_image(200, 120))
    qtbot.addWidget(window)
    # Physical pixels divided by the screen DPR -> logical window size.
    assert window.width() == round(200 / dpr)
    assert window.height() == round(120 / dpr)


def test_oversized_pin_is_scaled_to_fit_screen(qtbot):
    avail = QGuiApplication.primaryScreen().availableGeometry()
    huge = _image(avail.width() * 4, avail.height() * 4)
    window = PinnedWindow(huge)
    qtbot.addWidget(window)
    assert window.width() <= avail.width()
    assert window.height() <= avail.height()


def test_escape_closes_the_pin(qtbot):
    window = PinnedWindow(_image(100, 80))
    # Keep the C++ object alive past close() so qtbot teardown doesn't trip over
    # WA_DeleteOnClose; we only care that Esc dismisses (hides) the pin.
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    assert window.isVisible()
    # sendEvent routes straight to keyPressEvent regardless of offscreen focus.
    QApplication.sendEvent(window, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert not window.isVisible()


def test_double_click_closes_the_pin(qtbot):
    from PySide6.QtCore import QEvent as _QEvent
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent

    window = PinnedWindow(_image(100, 80))
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    pos = QPointF(5, 5)
    event = QMouseEvent(
        _QEvent.MouseButtonDblClick, pos, pos, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier
    )
    QApplication.sendEvent(window, event)
    assert not window.isVisible()


def test_fit_pixmap_caps_oversized_image_to_screen_fraction(qtbot):
    from PySide6.QtGui import QGuiApplication

    from shotquill.ui.pinned import _MAX_SCREEN_FRACTION, _fit_pixmap

    avail = QGuiApplication.primaryScreen().availableGeometry()
    dpr = QGuiApplication.primaryScreen().devicePixelRatio()
    huge = _image(avail.width() * 4, avail.height() * 4)
    pixmap = _fit_pixmap(huge)
    logical_w = pixmap.width() / dpr
    logical_h = pixmap.height() / dpr
    assert logical_w <= avail.width() * _MAX_SCREEN_FRACTION + 1
    assert logical_h <= avail.height() * _MAX_SCREEN_FRACTION + 1


def test_fit_pixmap_tags_device_pixel_ratio(qtbot):
    from PySide6.QtGui import QGuiApplication

    from shotquill.ui.pinned import _fit_pixmap

    dpr = QGuiApplication.primaryScreen().devicePixelRatio()
    pixmap = _fit_pixmap(_image(50, 40))
    assert pixmap.devicePixelRatio() == dpr


def test_drag_moves_the_window(qtbot):
    from PySide6.QtCore import QEvent as _QEvent
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent

    window = PinnedWindow(_image(100, 80))
    window.setAttribute(Qt.WA_DeleteOnClose, False)
    qtbot.addWidget(window)
    window.move(0, 0)

    pos = QPointF(5, 5)
    press = QMouseEvent(
        _QEvent.MouseButtonPress, pos, pos, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier
    )
    QApplication.sendEvent(window, press)
    assert window._drag_offset is not None

    release = QMouseEvent(
        _QEvent.MouseButtonRelease, pos, pos, Qt.LeftButton, Qt.NoButton, Qt.NoModifier
    )
    QApplication.sendEvent(window, release)
    assert window._drag_offset is None


def test_fit_pixmap_uses_the_given_screens_dpr(qtbot):
    from PySide6.QtCore import QRect as _QRect

    from shotquill.ui.pinned import _fit_pixmap

    class _FakeScreen:
        def devicePixelRatio(self):
            return 2.0

        def availableGeometry(self):
            return _QRect(0, 0, 5000, 5000)

    pixmap = _fit_pixmap(_image(100, 80), _FakeScreen())
    assert pixmap.devicePixelRatio() == 2.0


def test_pinned_window_accepts_an_origin_rect(qtbot):
    from PySide6.QtCore import QRect as _QRect

    # The origin selects the source screen (single screen offscreen: primary).
    window = PinnedWindow(_image(100, 80), _QRect(0, 0, 50, 40))
    qtbot.addWidget(window)
    dpr = QGuiApplication.primaryScreen().devicePixelRatio()
    assert window.width() == round(100 / dpr)


class _FakeConfig:
    def __init__(self, directory):
        self._dir = directory

    def save_dir(self):
        return self._dir

    def image_format(self):
        return "png"


def test_context_menu_lists_copy_save_close_with_config(qtbot, tmp_path):
    window = PinnedWindow(_image(100, 80), config=_FakeConfig(str(tmp_path)))
    qtbot.addWidget(window)
    labels = [a.text() for a in window._build_menu().actions() if a.text()]
    assert labels == [t("toolbar.copy"), t("toolbar.save"), t("pin.close")]


def test_context_menu_omits_save_without_config(qtbot):
    window = PinnedWindow(_image(100, 80))  # no config
    qtbot.addWidget(window)
    labels = [a.text() for a in window._build_menu().actions() if a.text()]
    assert labels == [t("toolbar.copy"), t("pin.close")]


def test_copy_places_the_full_resolution_image_on_the_clipboard(qtbot):
    # The display pixmap is scaled to fit the screen, but Copy must hand over the
    # original physical-resolution image, not the shrunk one.
    avail = QGuiApplication.primaryScreen().availableGeometry()
    huge = _image(avail.width() * 4, avail.height() * 4)
    window = PinnedWindow(huge)
    qtbot.addWidget(window)
    assert window.width() < huge.width()  # display really was downscaled

    window._copy()
    clip = QGuiApplication.clipboard().image()
    assert clip.width() == huge.width() and clip.height() == huge.height()


def test_save_writes_the_image_to_the_configured_folder(qtbot, tmp_path):
    window = PinnedWindow(_image(100, 80), config=_FakeConfig(str(tmp_path)))
    qtbot.addWidget(window)
    window._save()
    written = list(tmp_path.glob("*.png"))
    assert len(written) == 1 and written[0].stat().st_size > 0
