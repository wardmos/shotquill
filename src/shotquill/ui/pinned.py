# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""A "pinned" screenshot: a frameless, always-on-top window floating on the desktop.

Pinning keeps an annotated shot visible above other windows for reference. The
window is borderless and draggable anywhere on its surface. It stays chromeless
on purpose — the pin reads as just the image, not a titled window — so closing
and the other actions live on a right-click menu (Copy / Save / Close) instead
of window buttons; Esc or a double-click also dismiss it. The capture is at
physical (Retina) resolution, so we set the pixmap's device-pixel-ratio to the
screen's to show it at its on-screen size, and scale down anything larger than
the available screen so a full-screen pin still fits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QMessageBox, QWidget

from shotquill.i18n import t

if TYPE_CHECKING:
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage, QScreen

    from shotquill.config import Config

_MAX_SCREEN_FRACTION = 0.8


def _fit_pixmap(image: QImage, screen: QScreen | None = None) -> QPixmap:
    """Build a display pixmap: tagged with the screen DPR and capped to the screen.

    ``screen`` should be the display the shot came from — on mixed-DPR
    multi-monitor setups the primary screen's ratio would size shots from the
    other display wrongly. Falls back to the primary screen when unknown.
    """
    screen = screen or QGuiApplication.primaryScreen()
    dpr = screen.devicePixelRatio() if screen is not None else 1.0
    pixmap = QPixmap.fromImage(image)

    # Logical (point) size the physical pixels would occupy at this DPR.
    logical_w = pixmap.width() / dpr
    logical_h = pixmap.height() / dpr

    if screen is not None:
        avail = screen.availableGeometry().size()
        max_w = avail.width() * _MAX_SCREEN_FRACTION
        max_h = avail.height() * _MAX_SCREEN_FRACTION
        if logical_w > max_w or logical_h > max_h:
            scale = min(max_w / logical_w, max_h / logical_h)
            target = QSize(round(pixmap.width() * scale), round(pixmap.height() * scale))
            pixmap = pixmap.scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    pixmap.setDevicePixelRatio(dpr)
    return pixmap


class PinnedWindow(QWidget):
    """A draggable, always-on-top window showing a pinned screenshot."""

    def __init__(
        self, image: QImage, origin: QRect | None = None, config: Config | None = None
    ) -> None:
        super().__init__(
            None,
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool,
        )
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setToolTip(t("pin.tip"))

        # Keep the source image at full (physical) resolution for Copy / Save —
        # the display pixmap may be scaled down to fit the screen.
        self._image = image
        # ``config`` supplies the save folder / format for the menu's Save; the
        # action is omitted when it's absent (e.g. a config-less unit test).
        self._config = config

        # ``origin`` is the shot's on-screen rect (logical, global): use its
        # screen for DPR/size so a shot from a secondary display fits *that*
        # display, not the primary one.
        screen = QGuiApplication.screenAt(origin.center()) if origin is not None else None
        self._pixmap = _fit_pixmap(image, screen)
        self.setFixedSize(self._pixmap.deviceIndependentSize().toSize())
        self._drag_offset: QPoint | None = None

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._pixmap)
        # A hairline border so the pin reads as a distinct floating object.
        painter.setPen(QColor(0, 0, 0, 110))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_offset = None

    def mouseDoubleClickEvent(self, event) -> None:
        self.close()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def contextMenuEvent(self, event) -> None:
        # The chromeless pin has no window buttons, so right-click is the home
        # for its actions.
        self._build_menu().exec(event.globalPos())

    def _build_menu(self) -> QMenu:
        # Copy the shot, save it, or close. Save is only offered when a config
        # (save folder / format) is available.
        menu = QMenu(self)
        menu.addAction(t("toolbar.copy"), self._copy)
        if self._config is not None:
            menu.addAction(t("toolbar.save"), self._save)
        menu.addSeparator()
        menu.addAction(t("pin.close"), self.close)
        return menu

    def _copy(self) -> None:
        from shotquill.output.clipboard import copy_qimage

        copy_qimage(self._image)

    def _save(self) -> None:
        # Mirror the editor's save: write to the configured folder, and on
        # failure warn but keep the pin up so the shot isn't lost.
        from shotquill.output.saver import save_qimage

        try:
            save_qimage(self._image, self._config.save_dir(), self._config.image_format())
        except OSError as exc:
            QMessageBox.warning(self, "ShotQuill", t("notify.save_failed").format(error=exc))
