# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""The annotation editor window: screenshot + toolbar + copy/save.

By default it opens in *spotlight* mode: frameless (no macOS title bar or
traffic-light buttons) over a translucent dim layer covering the rest of the
desktop, so the shot stays lit in place exactly like the capture overlay
highlighted it. A Settings toggle restores the regular titled window.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QKeyCombination, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QGuiApplication,
    QImage,
    QKeySequence,
    QPainter,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import QLabel, QMainWindow, QSizePolicy, QWidget

from shotquill.i18n import key_display_name, t
from shotquill.ui.canvas import AnnotationCanvas
from shotquill.ui.toolbar import create_toolbar

if TYPE_CHECKING:
    from shotquill.config import Config

_MAX_INITIAL_WIDTH = 1400
_MAX_INITIAL_HEIGHT = 900

# Pure-modifier presses never match a finish key; ignore them outright.
_MODIFIER_KEYS = (Qt.Key_unknown, Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta)

# Same dim the capture overlay paints over the desktop, so the editor's
# backdrop reads as a continuation of the capture rather than a new layer.
_BACKDROP_DIM = QColor(0, 0, 0, 120)

# OCR status badge shown over the canvas in frameless mode (no title bar to
# carry the text); styled like the overlay's labels.
_BADGE_STYLE = (
    "background-color: rgba(0, 0, 0, 180); color: white;padding: 3px 8px; border-radius: 4px;"
)


class _EditorBackdrop(QWidget):
    """A translucent dim layer behind a frameless editor.

    Covers the whole virtual desktop with the same dim the capture overlay
    used, so editing feels like the capture's spotlight never went away: the
    shot stays lit while everything around it stays dark. Deliberately inert —
    it never takes focus (the editor keeps keyboard input) and clicks on it do
    nothing, so a stray click can't discard annotations. The editor shows and
    hides it with its own activation and closes it from ``closeEvent``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.Window
            | Qt.FramelessWindowHint
            | Qt.WindowDoesNotAcceptFocus
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setGeometry(QGuiApplication.primaryScreen().virtualGeometry())

    def paintEvent(self, event) -> None:
        QPainter(self).fillRect(self.rect(), _BACKDROP_DIM)


def _toolbar_placement(cursor: QPoint | None, origin: QRect | None) -> tuple[Qt.ToolBarArea, bool]:
    """Pick the toolbar's corner from where the pointer is: (area, right-align).

    The editor opens the instant a capture ends, so the pointer is still where
    the shot was confirmed — a region drag usually ends near the selection's
    bottom-right corner. Putting the toolbar in that corner saves the trip
    across the shot: bottom area when the pointer is in the capture's lower
    half, right-aligned when it is in the right half. Without an origin to
    compare against the toolbar stays at the top-left (the classic layout).
    """
    if cursor is None or origin is None or origin.isEmpty():
        return Qt.TopToolBarArea, False
    center = origin.center()
    area = Qt.BottomToolBarArea if cursor.y() > center.y() else Qt.TopToolBarArea
    return area, cursor.x() > center.x()


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
    #: Emitted when the user pins the shot to the desktop: the annotated image
    #: plus the capture's on-screen rect (or None) so the pin can size itself
    #: for the screen the shot came from.
    pin_requested = Signal(QImage, object)
    #: Internal: OCR finished on its worker thread — (lines, error). The queued
    #: delivery hops back to the GUI thread before touching clipboard/title.
    _ocr_done = Signal(object, object)

    def __init__(self, image: QImage, config: Config, origin: QRect | None = None) -> None:
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle(t("title.annotate"))
        self._config = config
        self._origin = origin
        self._placed = False

        # Spotlight mode (default, toggleable in Settings): the editor opens
        # frameless — no macOS title bar or traffic lights — over a dim
        # backdrop, so the shot stays lit in place against the darkened
        # desktop exactly like the capture overlay showed it.
        self._backdrop: _EditorBackdrop | None = None
        self._status_badge: QLabel | None = None
        if config.editor_backdrop():
            self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
            self._backdrop = _EditorBackdrop()

        pixmap = QPixmap.fromImage(image)
        self._canvas = AnnotationCanvas(pixmap)
        # The image is always fitted to the view (below and in resizeEvent), so
        # scrollbars never help — and the ~14px they'd steal would break the
        # canvas-over-capture alignment in _place_over_origin.
        self._canvas.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._canvas.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setCentralWidget(self._canvas)
        if self._backdrop is not None:
            # Frameless means no title bar to carry the OCR status text; a
            # small badge over the canvas shows it instead (see _set_status).
            self._status_badge = QLabel(self._canvas.viewport())
            self._status_badge.setStyleSheet(_BADGE_STYLE)
            self._status_badge.hide()
        toolbar = create_toolbar(self._canvas, self._copy, self._save, self._ocr, self._pin)
        # The toolbar lands in the corner nearest the pointer (e.g. the
        # bottom-right after a region drag towards the bottom of the screen),
        # so finishing a shot never means crossing the whole capture.
        area, align_right = _toolbar_placement(QCursor.pos(), origin)
        if align_right:
            spacer = QWidget()
            spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            toolbar.insertWidget(toolbar.actions()[0], spacer)
        self.addToolBar(area, toolbar)
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

        self._ocr_running = False
        self._ocr_done.connect(self._on_ocr_done)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._backdrop is not None:
            # Shown without activating, so the editor keeps keyboard focus;
            # raise_ keeps the editor above the dim layer.
            self._backdrop.show()
            self.raise_()
        if self._origin is not None and not self._placed:
            self._placed = True
            self._place_over_origin()
        self._canvas.fitInView(self._canvas.sceneRect(), Qt.KeepAspectRatio)

    def changeEvent(self, event) -> None:
        # The dim backdrop tracks the editor's activation: it hides while the
        # user is in another app (it must not darken whatever they switched
        # to) and comes back when the editor regains activation. Visibility is
        # guarded so the deactivation that accompanies closing can't resurrect
        # a backdrop that closeEvent already took down.
        if (
            event.type() == QEvent.ActivationChange
            and self._backdrop is not None
            and self.isVisible()
        ):
            if self.isActiveWindow():
                self._backdrop.show()
                self.raise_()
            else:
                self._backdrop.hide()
        super().changeEvent(event)

    def closeEvent(self, event) -> None:
        if self._backdrop is not None:
            self._backdrop.close()
            self._backdrop.deleteLater()
            self._backdrop = None
        super().closeEvent(event)

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
        # button and the finish key. On failure keep the editor open so the
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
        self.pin_requested.emit(self._canvas.export_image(), self._origin)
        self.close()

    def _ocr(self) -> None:
        # Vision's accurate recognition can take seconds on a full-screen shot,
        # so it runs on a worker thread instead of freezing the GUI; the title
        # shows progress. A second click while one is in flight is ignored.
        if self._ocr_running:
            return
        self._ocr_running = True
        self._set_status(t("title.ocr_running"))
        image = self._canvas.background_image()
        threading.Thread(target=self._run_ocr, args=(image,), daemon=True, name="sq-ocr").start()

    def _run_ocr(self, image: QImage) -> None:
        """Worker thread: recognize and hand the outcome back to the GUI thread."""
        from shotquill.ocr.macos import VisionTextRecognizer

        lines: list[str] | None = None
        error: Exception | None = None
        try:
            lines = VisionTextRecognizer().recognize(image)
        except Exception as exc:
            error = exc
        try:
            self._ocr_done.emit(lines, error)
        except RuntimeError:
            pass  # editor closed (and deleted) while OCR was in flight

    def _on_ocr_done(self, lines: object, error: object) -> None:
        from shotquill.output.clipboard import copy_text

        self._ocr_running = False
        if error is not None:
            self._set_status(t("title.ocr_failed").format(error=error))
        elif lines:
            copy_text("\n".join(lines))
            self._set_status(t("title.ocr_copied").format(count=len(lines)))
        else:
            self._set_status(t("title.ocr_empty"))

    def _set_status(self, text: str) -> None:
        """Surface OCR progress/outcome: the title bar carries it normally; in
        frameless spotlight mode (no title bar) a badge over the canvas does.
        The title is set either way so tests and tooling can read it."""
        self.setWindowTitle(text)
        if self._status_badge is None:
            return
        self._status_badge.setText(text)
        self._status_badge.adjustSize()
        self._status_badge.move(6, 6)
        self._status_badge.show()
        self._status_badge.raise_()
