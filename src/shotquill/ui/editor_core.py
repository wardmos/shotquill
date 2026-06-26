# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Shared annotation-editor core: canvas, toolbar, OCR, finish keys, crop logic.

:class:`EditorCoreMixin` holds everything an editor *shell* needs regardless of
how the shell presents itself — a framed/spotlight window (:class:`EditorWindow`)
or the full-screen unified surface (:class:`SpotlightSurface`). Both shells mix
this in and provide just the windowing-specific piece via one hook:
``place_for_selection(origin)`` — how the shot is re-placed after a crop change
(the framed window moves its top-level; the surface moves the canvas child).

The shell must, in its ``__init__``: declare the signals ``pin_requested`` and
``_ocr_done``; set ``self._status_badge`` (a QLabel over the canvas, or None);
call ``self._init_editor_core(...)`` then ``self._wire_adjust_hint()``.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, NamedTuple

from PySide6.QtCore import QKeyCombination, QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QImage, QKeySequence, QPixmap

from shotquill.i18n import adjust_hint_key, key_display_name, t
from shotquill.ui.canvas import AnnotationCanvas
from shotquill.ui.geometry import scale_rect_edges
from shotquill.ui.toolbar import create_toolbar

if TYPE_CHECKING:
    from shotquill.config import Config

# Keyboard adjustment of the crop: arrows step by one *native* pixel (what the
# size readout counts), Shift steps by _NUDGE_COARSE — the same stepping the
# capture overlay's loupe used to read in.
_NUDGE_COARSE = 10
_MIN_CROP = 2  # logical points; matches the overlay's minimum selection
_ARROW_DELTAS = {
    Qt.Key_Left: (-1, 0),
    Qt.Key_Right: (1, 0),
    Qt.Key_Up: (0, -1),
    Qt.Key_Down: (0, 1),
}

# Pure-modifier presses never match a finish key; ignore them outright.
_MODIFIER_KEYS = (Qt.Key_unknown, Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta)


class RegionContext(NamedTuple):
    """What a region capture must hand over for the crop to stay adjustable.

    ``screenshot`` is the frozen full-desktop shot (native pixels) the region
    was cropped from; ``geometry`` is the virtual desktop's rect in logical,
    global points — together they let the editor re-crop any selection.
    """

    screenshot: QImage
    geometry: QRect


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


class EditorCoreMixin:
    """Canvas + toolbar + OCR + finish-keys + crop adjustment, shell-agnostic.

    See the module docstring for the contract the shell must satisfy. Every
    method here uses only ``self`` attributes the shell sets up, plus the
    ``place_for_selection`` hook.
    """

    # --- setup (called from the shell's __init__) -------------------------

    def _init_editor_core(self, image, config, origin, region, recognizer, split_outputs=False):
        """Create the canvas + toolbar and wire OCR; return the toolbar.

        ``recognizer`` is passed in (not fetched here) so the shell decides it on
        a module the tests can patch. The shell positions the returned toolbar.

        ``split_outputs`` peels the copy/save buttons onto the toolbar's sibling
        ``outputs_toolbar`` so a width-constrained host can keep them visible (see
        create_toolbar); the shell must then place that bar too.
        """
        self._config = config
        self._origin = origin
        # Crop adjustment (region captures only): the live selection in logical
        # global points, kept as floats so native-pixel steps survive fractional
        # (Retina) scale factors; sx/sy convert to screenshot px.
        self._region = region if origin is not None else None
        self._selection = QRectF(origin) if self._region is not None else None
        if self._region is not None:
            self._region_sx = region.screenshot.width() / max(region.geometry.width(), 1)
            self._region_sy = region.screenshot.height() / max(region.geometry.height(), 1)

        self._canvas = AnnotationCanvas(QPixmap.fromImage(image))
        # The image is always fitted to the view, so scrollbars never help — and
        # the ~14px they'd steal would break the canvas-over-capture alignment.
        self._canvas.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._canvas.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # OCR is only offered when the platform has an on-device recognizer
        # (macOS Vision); elsewhere the action is omitted (None).
        self._recognizer = recognizer
        self._toolbar = create_toolbar(
            self._canvas,
            self._copy,
            self._save,
            self._ocr if recognizer is not None else None,
            self._pin,
            style=config.toolbar_style(),
            split_outputs=split_outputs,
        )
        self._copy_action = self._toolbar.copy_action
        self._save_action = self._toolbar.save_action

        self._ocr_running = False
        self._hint_showing = False
        self._ocr_done.connect(self._on_ocr_done)
        return self._toolbar

    def _wire_adjust_hint(self) -> None:
        if self._can_adjust():
            # Make the merged adjust+annotate mode discoverable; the hint is
            # retired once the first annotation freezes the crop.
            self._canvas.undo_stack().indexChanged.connect(self._retire_adjust_hint)
            self._show_adjust_hint()

    # --- finish keys ------------------------------------------------------

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

    def handle_key(self, event) -> bool:
        """Consume a crop-nudge arrow or a finish key; return True if handled.

        The shell's ``keyPressEvent`` calls this first, then falls back to
        ``super().keyPressEvent``.
        """
        # Crop adjustment first: until the first annotation lands, the arrow keys
        # nudge a region capture's crop (⇧ steps by 10, ⌥ resizes). The canvas
        # declines plain arrows so they reach the shell.
        if event.key() in _ARROW_DELTAS and self._can_adjust():
            self._adjust_crop(event)
            return True
        if self._canvas.handle_delete_key(event):
            return True
        # Quick-finish keys (configurable; Space copies, Enter saves by default).
        # Both close the editor. A focused text annotation consumes the key
        # first, so these never fire mid-typing.
        pressed = _pressed_sequence(event)
        if not pressed.isEmpty():
            if pressed == self._copy_key:
                self._copy()
                return True
            if pressed == self._save_key:
                self._save()
                return True
        return False

    # --- crop adjustment (region captures, until the first annotation) ----

    def _can_adjust(self) -> bool:
        return self._region is not None and self._canvas.is_pristine()

    def crop_adjustable(self) -> bool:
        return self._can_adjust()

    def _crop_bounds(self) -> QRectF:
        """The rect the crop may roam in — the whole captured desktop by default.

        A shell that only covers ONE screen (the spotlight surface) narrows this
        to that screen, so a keyboard nudge can't push the crop off the window it
        lives in (which would slide the canvas child out of view). The framed
        window re-places its top-level, so it keeps the full desktop.
        """
        return QRectF(self._region.geometry)

    def _adjust_crop(self, event) -> None:
        dx, dy = _ARROW_DELTAS[event.key()]
        step = _NUDGE_COARSE if event.modifiers() & Qt.ShiftModifier else 1
        # One step is one *native* pixel expressed in logical points, so on a
        # Retina screen a press moves the crop (and the size readout) by exactly
        # one screenshot pixel, not one 2x point.
        lx = dx * step / self._region_sx
        ly = dy * step / self._region_sy
        sel = QRectF(self._selection)
        bounds = self._crop_bounds()
        if event.modifiers() & Qt.AltModifier:
            # Option+arrows move the right/bottom edge; combined with plain
            # arrows (which move the whole box) any edge can be placed exactly.
            sel.setRight(min(max(sel.right() + lx, sel.left() + _MIN_CROP), bounds.right()))
            sel.setBottom(min(max(sel.bottom() + ly, sel.top() + _MIN_CROP), bounds.bottom()))
        else:
            sel.moveLeft(min(max(sel.left() + lx, bounds.left()), bounds.right() - sel.width()))
            sel.moveTop(min(max(sel.top() + ly, bounds.top()), bounds.bottom() - sel.height()))
        self._selection = sel
        self._apply_selection()

    def _apply_selection(self) -> None:
        """Re-crop the adjusted selection, then let the shell re-place the shot."""
        origin = self.recrop_selection()
        self.place_for_selection(origin)

    def recrop_selection(self) -> QRect:
        """Re-crop from the frozen screenshot into the canvas; return the new origin.

        Windowing-free: the shell's ``place_for_selection`` does the re-placement.
        """
        region = self._region
        relative = (
            self._selection.x() - region.geometry.x(),
            self._selection.y() - region.geometry.y(),
            self._selection.width(),
            self._selection.height(),
        )
        # Crop by edges from the float selection, exactly as the capture overlay
        # did, so fractional scale factors never clip a pixel row.
        phys = QRect(*scale_rect_edges(relative, self._region_sx, self._region_sy))
        cropped = region.screenshot.copy(phys.intersected(region.screenshot.rect()))
        self._canvas.set_background(QPixmap.fromImage(cropped))
        # The same int snap the overlay applied to the rect it emitted.
        self._origin = QRect(
            int(self._selection.x()),
            int(self._selection.y()),
            int(self._selection.width()),
            int(self._selection.height()),
        )
        return self._origin

    def _show_adjust_hint(self) -> None:
        self._set_status(t(adjust_hint_key()))
        self._hint_showing = True

    def _retire_adjust_hint(self) -> None:
        # The first annotation freezes the crop; take the stale hint down with
        # it — unless OCR or another status has already replaced it.
        if not self._hint_showing or self._canvas.is_pristine():
            return
        self._hint_showing = False
        self.setWindowTitle(t("title.annotate"))
        if self._status_badge is not None:
            self._status_badge.hide()

    # --- outputs (toolbar buttons + finish keys share these) --------------

    def _copy(self) -> None:
        # Copy the annotated shot to the clipboard, then close the editor.
        from shotquill.output.clipboard import copy_qimage

        copy_qimage(self._canvas.export_image())
        self.close()

    def _save(self) -> None:
        # Save to the configured folder, then close. On failure keep the editor
        # open so the annotations aren't lost.
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
        if self._ocr_running or self._recognizer is None:
            return
        self._ocr_running = True
        self._set_status(t("title.ocr_running"))
        image = self._canvas.background_image()
        # Snapshot the recognizer on the GUI thread and hand it to the worker, so
        # the worker never reads live ``self`` state that could be swapped out
        # from under it (mirrors how ``image`` is already passed in).
        recognizer = self._recognizer
        threading.Thread(
            target=self._run_ocr, args=(recognizer, image), daemon=True, name="sq-ocr"
        ).start()

    def _run_ocr(self, recognizer, image: QImage) -> None:
        """Worker thread: recognize and hand the outcome back to the GUI thread."""
        lines: list[str] | None = None
        error: Exception | None = None
        try:
            lines = recognizer.recognize(image)
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
        self._hint_showing = False  # any real status outranks the adjust hint
        self.setWindowTitle(text)
        if self._status_badge is None:
            return
        self._status_badge.setText(text)
        self._status_badge.adjustSize()
        self._status_badge.move(6, 6)
        self._status_badge.show()
        self._status_badge.raise_()
