# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""UI debug helpers.

These route through the app-wide debug logger, so crop-adjust diagnostics follow
the same debug-mode switch and platform log file as every other subsystem.
"""

from __future__ import annotations

from shotquill import debug_log

_LOG = debug_log.get_logger(__name__)


def crop_log(message: str) -> None:  # pragma: no cover - opt-in diagnostic
    try:
        _LOG.debug("crop %s", message)
    except Exception:
        pass  # diagnostics must never break the app
