# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
import sys

# Route through the CLI so `python -m shotquill capture` behaves exactly like
# `squill capture` (the pre-CLI version imported the GUI app here, silently
# ignoring every argument). Bare invocation still launches the GUI via main().
from shotquill.cli import main

if __name__ == "__main__":
    sys.exit(main())
