# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Convert the committed master PNG icon to a multi-size Windows ``.ico``.

PyInstaller's ``--icon`` wants an ``.ico`` (Qt's QImage can't write that
format), so this is a build-time helper rather than a committed binary asset.
It needs Pillow (``pip install pillow``); the build script calls it best-effort
and falls back to no custom icon if Pillow is absent.

Usage: ``python packaging/windows/make_icon.py [out.ico]``
"""

from __future__ import annotations

import sys
from pathlib import Path

# The same master PNG the macOS iconset is rendered from, so all platforms show
# one mark.
_SOURCE = Path(__file__).resolve().parent.parent / "macos" / "icon.png"
# The standard Windows icon sizes; Explorer / the taskbar pick the right one.
_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main(argv: list[str]) -> int:
    out = Path(argv[0]) if argv else Path(__file__).resolve().parent / "icon.ico"
    try:
        from PIL import Image
    except ImportError:
        print("pillow is required to build the .ico (pip install pillow)", file=sys.stderr)
        return 1
    if not _SOURCE.is_file():
        print(f"source icon not found: {_SOURCE}", file=sys.stderr)
        return 1
    image = Image.open(_SOURCE).convert("RGBA")
    image.save(out, format="ICO", sizes=_SIZES)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
