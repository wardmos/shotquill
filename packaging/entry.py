# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""PyInstaller entry point for the bundled macOS app."""

import sys

from shotquill.app import run

if __name__ == "__main__":
    sys.exit(run())
