# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""PyInstaller entry point for the bundled macOS app.

One binary serves both faces: ``cli.main`` opens the menu-bar GUI when
invoked bare (Finder double-click, ``open -a``) and runs the headless CLI
when given arguments — which is how the PKG's optional links and the Homebrew
cask reach ``capture``/``mcp``/``doctor`` inside the bundle.
"""

import sys

from shotquill.cli import main

if __name__ == "__main__":
    sys.exit(main())
