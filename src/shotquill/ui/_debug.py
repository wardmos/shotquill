# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Opt-in UI diagnostics.

Off by default. Set ``SHOTQUILL_CROP_DEBUG=1`` before launching to append a line
per crop-adjust decision (press routing, overlay entry, re-crop, window resize)
to ``<temp>/shotquill/crop-debug.log``. This pins down macOS-only crop edge-drag
behaviour that cannot be reproduced on a headless / non-macOS host. The log holds
only geometry (sizes and coordinates), never image pixels.

Set ``SHOTQUILL_ANNOTATION_DEBUG=1`` to append annotation editing decisions to
``<temp>/shotquill/annotation-debug.log``.
"""

from __future__ import annotations

import os

_ENABLED = bool(os.environ.get("SHOTQUILL_CROP_DEBUG"))
_ANNOTATION_ENABLED = bool(os.environ.get("SHOTQUILL_ANNOTATION_DEBUG"))


def crop_log(message: str) -> None:  # pragma: no cover - opt-in diagnostic
    _write_log("crop-debug.log", message, enabled=_ENABLED)


def annotation_log(message: str) -> None:  # pragma: no cover - opt-in diagnostic
    _write_log("annotation-debug.log", message, enabled=_ANNOTATION_ENABLED)


def _write_log(filename: str, message: str, *, enabled: bool) -> None:  # pragma: no cover
    if not enabled:
        return
    try:
        from shotquill.paths import capture_tmp_dir

        with (capture_tmp_dir() / filename).open("a", encoding="utf-8") as fh:
            fh.write(message + "\n")
    except Exception:
        pass  # diagnostics must never break the app
