# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""The annotation editor window: screenshot + toolbar + copy/save."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import QMainWindow

from shotquill.ui.canvas import AnnotationCanvas
from shotquill.ui.toolbar import create_toolbar

if TYPE_CHECKING:
    from PySide6.QtGui import QImage

    from shotquill.config import Config

_MAX_INITIAL_WIDTH = 1400
_MAX_INITIAL_HEIGHT = 900


class EditorWindow(QMainWindow):
    def __init__(self, image: QImage, config: Config) -> None:
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("Shotquill — 标注")
        self._config = config

        pixmap = QPixmap.fromImage(image)
        self._canvas = AnnotationCanvas(pixmap)
        self.setCentralWidget(self._canvas)
        self.addToolBar(create_toolbar(self._canvas, self._copy, self._save))

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
        self.setWindowTitle("Shotquill — 已复制到剪贴板")

    def _save(self) -> None:
        from shotquill.output.saver import save_qimage

        path = save_qimage(
            self._canvas.export_image(),
            self._config.save_dir(),
            self._config.image_format(),
        )
        self.setWindowTitle(f"Shotquill — 已保存 {path.name}")
