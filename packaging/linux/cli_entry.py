# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""PyInstaller entry point for the Linux AppImage (CLI/MCP only).

There is no menu-bar GUI on Linux yet, so — unlike the macOS bundle, whose bare
launch opens the app — a bare AppImage run shows ``--help`` instead of
``cli.main()``'s default of starting the macOS-only app. With arguments it is
the ordinary ``squill`` CLI (``capture`` / ``mcp`` / ``doctor`` / …).
"""

import sys

from shotquill.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["--help"]))
