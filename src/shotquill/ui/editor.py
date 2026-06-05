# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""The annotation editor window: screenshot + toolbar + copy/save."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QKeyCombination, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QGuiApplication, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import QMainWindow

from shotquill.i18n import key_display_name, t
from shotquill.ui.canvas import AnnotationCanvas
from shotquill.ui.toolbar import create_toolbar

if TYPE_CHECKING:
    from shotquill.config import Config

_MAX_INITIAL_WIDTH = 1400
_MAX_INITIAL_HEIGHT = 900

# Pure-modifier presses never match a finish key; ignore them outright.
_MODIFIER_KEYS = (Qt.Key_unknown, Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta)


def _finish_sequence(config: Config, action: str) -> QKeySequence:
    """The configured finish-key sequence, or an empty one when disabled."""
    if not config.hotkey_enabled(action):
        return QKeySequence()
    return QKeySequence(config.editor_hotkey(action))


def _pressed_sequence(event) -> QKeySequence:
    """Normalize a key event into a QKeySequence for finish-key matching."""
    key = event.key()
    if key in _MODIFIER_KEYS:
        return QKeySequence()
    if key == Qt.Key_Enter:
        key = Qt.Key_Return  # keypad Enter counts as a configured Return
    modifiers = event.modifiers() & ~Qt.KeypadModifier
    return QKeySequence(QKeyCombination(modifiers, Qt.Key(key)))


def _finish_tip(sequence: QKeySequence, label: str) -> str:
    """A tooltip with the finish-key name appended (omitted when the key is off).

    NativeText keeps macOS modifier symbols unambiguous (⌘D — the portable
    "Ctrl+D" would be misleading there because Qt swaps Ctrl/Cmd). The lookup
    for a localized key name must use the *portable* spelling though: on macOS
    NativeText renders Return as ↩, which the display-name table would never
    match. So the localized name replaces the key's native suffix (macOS
    "⌘↩" → "⌘回车", elsewhere "Ctrl+Return" → "Ctrl+回车").
    """
    native = sequence.toString(QKeySequence.NativeText)
    if not native:
        return label
    portable_key = sequence.toString().split("+")[-1]  # the non-modifier key
    localized = key_display_name(portable_key)
    if localized != portable_key:
        native_key = QKeySequence(portable_key).toString(QKeySequence.NativeText)
        if native_key and native.endswith(native_key):
            native = native[: len(native) - len(native_key)] + localized
    return f"{label} ({native})"


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
        toolbar = create_toolbar(self._canvas, self._copy, self._save, self._ocr, self._pin)
        self.addToolBar(toolbar)
        self._copy_action = toolbar.copy_action
        self._save_action = toolbar.save_action
        # Resolves the (configurable, possibly disabled) finish keys and sets
        # the matching tooltips; re-run by the app whenever Settings changes.
        self.reload_finish_keys()

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

    def reload_finish_keys(self) -> None:
        """Re-resolve the finish keys from config — the app calls this on every
        open editor after the user accepts the Settings dialog, so changed or
        disabled keys take effect without reopening the window."""
        self._copy_key = _finish_sequence(self._config, "editor_copy")
        self._save_key = _finish_sequence(self._config, "editor_save")
        self._refresh_finish_tips()

    def _refresh_finish_tips(self) -> None:
        self._copy_action.setToolTip(_finish_tip(self._copy_key, t("toolbar.copy_tip")))
        self._save_action.setToolTip(_finish_tip(self._save_key, t("toolbar.save_tip")))

    def keyPressEvent(self, event) -> None:
        # Quick-finish keys (configurable; Space copies to the clipboard and
        # Enter saves to disk by default). Both close the editor (handled in
        # _copy / _save). A focused text annotation consumes the key first, so
        # these never fire mid-typing.
        pressed = _pressed_sequence(event)
        if not pressed.isEmpty():
            if pressed == self._copy_key:
                self._copy()
                return
            if pressed == self._save_key:
                self._save()
                return
        super().keyPressEvent(event)

    def _copy(self) -> None:
        # Copy the annotated shot to the clipboard, then close the editor — the
        # toolbar button and the finish key share this finish-and-dismiss flow.
        from shotquill.output.clipboard import copy_qimage

        copy_qimage(self._canvas.export_image())
        self.close()

    def _save(self) -> None:
        # Save to the configured folder, then close — shared by the toolbar
        # button and the finish key.
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
