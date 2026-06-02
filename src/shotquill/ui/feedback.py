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
    """Plays a capture flash and/or sound; keeps the flash window alive while it fades."""

    def __init__(self) -> None:
        self._flash: QWidget | None = None
        self._animation: QPropertyAnimation | None = None

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
        animation.finished.connect(self._on_finished)

        self._flash = window
        self._animation = animation

        window.setWindowOpacity(_FLASH_PEAK_OPACITY)
        window.show()
        animation.start()

    def _on_finished(self) -> None:
        if self._flash is not None:
            self._flash.close()
        self._flash = None
        self._animation = None
