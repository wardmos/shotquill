# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""The annotation editor window: screenshot + toolbar + copy/save."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QKeySequence, QPixmap, QShortcut
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

    def __init__(self, image: QImage, config: Config) -> None:
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(t("title.annotate"))
        self._config = config

        pixmap = QPixmap.fromImage(image)
        self._canvas = AnnotationCanvas(pixmap)
        self.setCentralWidget(self._canvas)
        self.addToolBar(create_toolbar(self._canvas, self._copy, self._save, self._ocr, self._pin))

        self.resize(
            min(pixmap.width(), _MAX_INITIAL_WIDTH) + 40,
            min(pixmap.height(), _MAX_INITIAL_HEIGHT) + 120,
        )

        close_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        close_shortcut.activated.connect(self.close)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._canvas.fitInView(self._canvas.sceneRect(), Qt.KeepAspectRatio)

    def _copy(self) -> None:
        from shotquill.output.clipboard import copy_qimage

        copy_qimage(self._canvas.export_image())
        self.setWindowTitle(t("title.copied"))

    def _save(self) -> None:
        from shotquill.output.saver import save_qimage

        path = save_qimage(
            self._canvas.export_image(),
            self._config.save_dir(),
            self._config.image_format(),
        )
        self.setWindowTitle(t("title.saved").format(name=path.name))

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
