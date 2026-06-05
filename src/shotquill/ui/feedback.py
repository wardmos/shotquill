# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Capture feedback: a brief screen flash and an optional shutter sound.

The flash is a frameless, click-through white overlay over the captured area
that fades out in a fraction of a second — a lightweight "the shot was taken"
cue with no bundled asset. The sound is opt-in and uses the system beep so we
don't ship an audio file. Both are gated by user config in the caller.
"""

from __future__ import annotations

from PySide6.QtCore import QPropertyAnimation, QRect, Qt
from PySide6.QtWidgets import QApplication, QWidget

_FLASH_PEAK_OPACITY = 0.55
_FLASH_DURATION_MS = 180


class CaptureFeedback:
    """Plays a capture flash and/or sound; keeps each flash window alive while it fades."""

    def __init__(self) -> None:
        # Flashes still fading. Rapid captures can overlap two of them, so each
        # animation closes only its own window — a shared "current flash" slot
        # would let an old animation close the new flash and leak the old one.
        # This list just keeps Python references alive until close/deletion.
        self._flashes: list[QWidget] = []

    def trigger(self, geometry: QRect, *, flash: bool, sound: bool) -> None:
        """Show the flash over ``geometry`` and/or beep, per the given toggles."""
        if sound:
            QApplication.beep()
        if flash:
            self._show_flash(geometry)

    def _show_flash(self, geometry: QRect) -> None:
        window = QWidget(
            None,
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool,
        )
        window.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        window.setAttribute(Qt.WA_ShowWithoutActivating, True)
        window.setAttribute(Qt.WA_DeleteOnClose, True)
        window.setStyleSheet("background-color: white;")
        window.setGeometry(geometry)

        animation = QPropertyAnimation(window, b"windowOpacity", window)
        animation.setDuration(_FLASH_DURATION_MS)
        animation.setStartValue(_FLASH_PEAK_OPACITY)
        animation.setEndValue(0.0)
        animation.finished.connect(window.close)
        window.destroyed.connect(lambda: self._discard(window))

        self._flashes.append(window)
        window.setWindowOpacity(_FLASH_PEAK_OPACITY)
        window.show()
        animation.start()

    def _discard(self, window: QWidget) -> None:
        if window in self._flashes:
            self._flashes.remove(window)
