# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""The annotation editor window: screenshot + toolbar + copy/save."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import QMainWindow

from shotquill.i18n import t
from shotquill.ui.canvas import AnnotationCanvas
from shotquill.ui.toolbar import create_toolbar

if TYPE_CHECKING:
    from shotquill.config import Config

_MAX_INITIAL_WIDTH = 1400
_MAX_INITIAL_HEIGHT = 900


class EditorWindow(QMainWindow):
    #: Emitted with the annotated image when the user pins the shot to the desktop.
    pin_requested = Signal(QImage)

    def __init__(self, image: QImage, config: Config, origin: QRect | None = None) -> None:
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(t("title.annotate"))
        self._config = config
        self._origin = origin
        self._placed = False

        pixmap = QPixmap.fromImage(image)
        self._canvas = AnnotationCanvas(pixmap)
        # The image is always fitted to the view (below and in resizeEvent), so
        # scrollbars never help — and the ~14px they'd steal would break the
        # canvas-over-capture alignment in _place_over_origin.
        self._canvas.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._canvas.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setCentralWidget(self._canvas)
        self.addToolBar(create_toolbar(self._canvas, self._copy, self._save, self._ocr, self._pin))

        # Size from the shot's on-screen (logical) rect when known — the pixmap
        # is in native pixels, which is 2x too large on Retina displays.
        initial = origin.size() if origin is not None else pixmap.size()
        self.resize(
            min(initial.width(), _MAX_INITIAL_WIDTH) + 40,
            min(initial.height(), _MAX_INITIAL_HEIGHT) + 120,
        )

        close_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        close_shortcut.activated.connect(self.close)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._origin is not None and not self._placed:
            self._placed = True
            self._place_over_origin()
        self._canvas.fitInView(self._canvas.sceneRect(), Qt.KeepAspectRatio)

    def resizeEvent(self, event) -> None:
        # Keep the whole image fitted as the user resizes — with scrollbars off
        # there is no other way to reach content outside the viewport.
        super().resizeEvent(event)
        self._canvas.fitInView(self._canvas.sceneRect(), Qt.KeepAspectRatio)

    def _place_over_origin(self) -> None:
        """Open the editor so the screenshot appears to stay where it was shot.

        Sizes the window so the canvas viewport matches the capture's on-screen
        size (within the initial-size caps), shifts the frame so the canvas
        lands exactly on the capture rect, then clamps the frame to the screen
        so the toolbar and window edges stay reachable. Runs on first show —
        only then are the toolbar and frame dimensions known.
        """
        self.layout().activate()  # settle toolbar/central layout before measuring
        viewport = self._canvas.viewport()
        target = QSize(
            min(self._origin.width(), _MAX_INITIAL_WIDTH),
            min(self._origin.height(), _MAX_INITIAL_HEIGHT),
        )
        self.resize(self.size() + (target - viewport.size()))

        delta = self._origin.topLeft() - viewport.mapToGlobal(QPoint(0, 0))
        frame = self.frameGeometry().translated(delta)
        screen = QGuiApplication.screenAt(self._origin.center())
        available = (screen or self.screen()).availableGeometry()
        max_left = max(available.left(), available.right() - frame.width() + 1)
        max_top = max(available.top(), available.bottom() - frame.height() + 1)
        frame.moveLeft(min(max(frame.left(), available.left()), max_left))
        frame.moveTop(min(max(frame.top(), available.top()), max_top))
        self.move(self.pos() + (frame.topLeft() - self.frameGeometry().topLeft()))

    def keyPressEvent(self, event) -> None:
        # Quick-finish keys: Space saves to disk, Enter copies to the clipboard.
        # Both close the editor (handled in _save / _copy). A focused text
        # annotation consumes the key first, so these never fire mid-typing.
        key = event.key()
        if key == Qt.Key_Space:
            self._save()
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            self._copy()
        else:
            super().keyPressEvent(event)

    def _copy(self) -> None:
        # Copy the annotated shot to the clipboard, then close the editor — the
        # toolbar button and the Enter shortcut share this finish-and-dismiss flow.
        from shotquill.output.clipboard import copy_qimage

        copy_qimage(self._canvas.export_image())
        self.close()

    def _save(self) -> None:
        # Save to the configured folder, then close — shared by the toolbar
        # button and the Space shortcut. On failure keep the editor open so the
        # annotations aren't lost.
        from PySide6.QtWidgets import QMessageBox

        from shotquill.output.saver import save_qimage

        try:
            save_qimage(
                self._canvas.export_image(),
                self._config.save_dir(),
                self._config.image_format(),
            )
        except OSError as exc:
            QMessageBox.warning(self, "ShotQuill", t("notify.save_failed").format(error=exc))
            return
        self.close()

    def _pin(self) -> None:
        # Hand the annotated image to the app (which keeps the pin alive), then
        # close the editor — the floating pin replaces it.
        self.pin_requested.emit(self._canvas.export_image())
        self.close()

    def _ocr(self) -> None:
        from shotquill.ocr.macos import VisionTextRecognizer
        from shotquill.output.clipboard import copy_text

        try:
            lines = VisionTextRecognizer().recognize(self._canvas.background_image())
        except Exception as exc:
            self.setWindowTitle(t("title.ocr_failed").format(error=exc))
            return
        if lines:
            copy_text("\n".join(lines))
            self.setWindowTitle(t("title.ocr_copied").format(count=len(lines)))
        else:
            self.setWindowTitle(t("title.ocr_empty"))
