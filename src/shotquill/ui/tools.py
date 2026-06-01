# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Annotation tool identifiers."""

from __future__ import annotations

from enum import Enum, auto


class Tool(Enum):
    SELECT = auto()
    RECT = auto()
    ELLIPSE = auto()
    ARROW = auto()
    LINE = auto()
    PEN = auto()
    HIGHLIGHTER = auto()
    MOSAIC = auto()
    TEXT = auto()
