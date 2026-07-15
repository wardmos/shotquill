# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Session-level emergency Escape handling for screenshot surfaces."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QWidget


class CaptureEscapeGuard(QObject):
    """Catch Escape before the focused child can consume it.

    Screenshot mode moves focus through graphics text items, colour dialogs,
    and sometimes a separate crop-adjust window. A window-scoped ``QShortcut``
    cannot see all of those states. Enabling this object on the application
    puts the emergency exit ahead of the focused widget's own key handler while
    keeping the hook strictly bounded to the owning screenshot session.
    """

    def __init__(self, owner: QWidget, cancel: Callable[[], None]) -> None:
        super().__init__(owner)
        self._owner: QWidget | None = owner
        self._app = QApplication.instance()
        self._cancel: Callable[[], None] | None = cancel
        self._installed = False

    def enable(self) -> None:
        """Start intercepting keys when the screenshot surface is presented."""
        if self._app is not None and self._cancel is not None and not self._installed:
            self._app.installEventFilter(self)
            self._installed = True

    def disable(self) -> None:
        """Stop intercepting keys as soon as the screenshot session closes."""
        self._owner = None
        app, self._app = self._app, None
        self._cancel = None
        if app is not None and self._installed:
            app.removeEventFilter(self)
        self._installed = False

    def eventFilter(self, watched, event) -> bool:
        cancel = self._cancel
        if cancel is not None and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            # Accept before closing: a child QDialog must not also process the
            # same press as reject() while its owner is tearing down. Explicitly
            # close owned top-levels first; hiding a parent widget alone does not
            # immediately hide a child dialog on every platform.
            event.accept()
            self._close_owned_windows()
            cancel()
            return True
        return super().eventFilter(watched, event)

    def _close_owned_windows(self) -> None:
        owner = self._owner
        app = self._app
        if owner is None or app is None:
            return
        for window in app.topLevelWidgets():
            if window is owner:
                continue
            parent = window.parentWidget()
            while parent is not None and parent is not owner:
                parent = parent.parentWidget()
            if parent is owner:
                window.close()
