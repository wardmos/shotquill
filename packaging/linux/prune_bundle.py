#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Prune dead shared libraries and plugins from the PyInstaller AppImage tree.

``--exclude-module`` keeps a Qt *module* out of the bundle, but PyInstaller's
PySide6 hook still copies whole subtrees of shared libraries and Qt plugins that
nothing on the CLI/MCP path loads — the QML/Quick engine, QtPdf, the GTK
platform theme (which drags in the entire GTK/Pango/ATK/Cairo stack), and so on.
On a fresh 6.11 build that is ~45 MB of dead weight in a ~154 MB tree.

Rather than hardcode a list of files to delete (which silently no-ops when a Qt
upgrade renames or relocates something — the exact drift the macOS PKG guard
warns about), this prunes by *reachability*. It seeds a root set with everything
that can actually be an entry point at run time — the PyInstaller executable, the
embedded ``libpython``, every Python C-extension (``*.cpython-*.so`` /
``*.abi3.so``), and the Qt plugins we keep — then walks ``DT_NEEDED`` transitively
over the bundled ``.so`` files. Any bundled library never reached that way is, by
construction, loaded by no kept code, so removing it cannot break a kept path.

Plugins are ``dlopen``-ed by name (never named in a ``DT_NEEDED``), so they are
pruned by an explicit keep policy (below) instead; a dropped plugin is never a
dependency of a kept library, so dropping it is always safe.

The CLI/MCP AppImage needs: QtCore/QtGui/QtDBus, a platform plugin to construct
``QGuiApplication`` (``wayland`` for the portal backend's real session, ``xcb``
for the opt-in ``QT_QPA_PLATFORM=xcb`` grab, ``offscreen`` for tests, ``minimal``
as a fallback), and the JPEG image plugin (PNG is built into QtGui).

Usage: prune_bundle.py <pyinstaller-dist-dir>   # e.g. build/appimage/dist/squill
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Platform plugins worth keeping; everything else under platforms/ (eglfs,
# linuxfb, vnc, minimalegl, vkkhrdisplay — embedded/special targets) goes.
_KEEP_PLATFORMS = {"libqxcb.so", "libqwayland.so", "libqoffscreen.so", "libqminimal.so"}
# Image plugins worth keeping. PNG is built into QtGui; JPEG needs this plugin.
_KEEP_IMAGEFORMATS = {"libqjpeg.so"}


def _keep_plugin(path: Path) -> bool:
    """Whether a Qt plugin under plugins/ stays in the bundle."""
    category = path.parent.name
    name = path.name
    if category == "imageformats":
        return name in _KEEP_IMAGEFORMATS
    if category == "platforms":
        return name in _KEEP_PLATFORMS
    if category == "platformthemes":
        return "libqgtk3" not in name  # no native GTK theming on a CLI
    if category == "iconengines":
        return "libqsvgicon" not in name  # QtSvg is excluded
    if category == "platforminputcontexts":
        return "virtualkeyboard" not in name.lower()  # pulls QtQuick
    if category == "egldeviceintegrations":
        return False  # eglfs support; no embedded EGL target here
    return True  # wayland-*, xcbglintegrations, generic, …


def _needed(path: Path) -> list[str]:
    """The DT_NEEDED sonames of an ELF file (empty for non-ELF / on error)."""
    try:
        out = subprocess.run(
            ["readelf", "-d", str(path)], capture_output=True, text=True, check=True
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    needed = []
    for line in out.splitlines():
        if "(NEEDED)" in line and "[" in line:
            needed.append(line.split("[", 1)[1].split("]", 1)[0])
    return needed


def _is_elf_so(path: Path) -> bool:
    return path.is_file() and not path.is_symlink() and ".so" in path.name


def prune(dist: Path) -> int:
    """Prune the tree in place; return the number of bytes reclaimed."""
    # Reachability is only sound if we can actually read DT_NEEDED. Without
    # readelf every walk would come back empty, every Qt lib would look orphan,
    # and we'd gut the bundle — refuse rather than ship a broken AppImage.
    try:
        subprocess.run(["readelf", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        sys.exit("error: readelf (binutils) is required to prune the bundle safely")

    # Index every bundled real .so by basename so DT_NEEDED sonames resolve to
    # the in-bundle copy (PyInstaller flattens libs, so basename is enough).
    by_name: dict[str, Path] = {}
    for path in dist.rglob("*"):
        if _is_elf_so(path):
            by_name.setdefault(path.name, path)

    plugins_dir = dist / "_internal/PySide6/Qt/plugins"
    dropped_plugins = [
        p for p in plugins_dir.rglob("*.so") if not p.is_symlink() and not _keep_plugin(p)
    ]

    # Seed roots with every runtime entry point, then close over DT_NEEDED.
    roots: list[Path] = [dist / "squill"]
    for path in dist.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        if (
            path.name.startswith("libpython")
            or path.name.endswith(".abi3.so")
            or ".cpython-" in path.name
        ):
            roots.append(path)
    kept_plugins = [p for p in plugins_dir.rglob("*.so") if not p.is_symlink() and _keep_plugin(p)]
    roots.extend(kept_plugins)

    reachable: set[Path] = set()
    stack: list[Path] = []
    for root in roots:
        rp = root.resolve()
        if rp not in reachable:
            reachable.add(rp)
            stack.append(root)
    while stack:
        for soname in _needed(stack.pop()):
            target = by_name.get(soname)
            if target and target.resolve() not in reachable:
                reachable.add(target.resolve())
                stack.append(target)

    # Anything bundled but unreachable is loaded by no kept code.
    orphan_libs = [p for p in by_name.values() if p.resolve() not in reachable]

    reclaimed = 0
    for path in [*orphan_libs, *dropped_plugins]:
        if path.exists():
            reclaimed += path.stat().st_size
            path.unlink()

    # Qt UI translations: the CLI is English-only (mirrors the macOS PKG prune).
    translations = dist / "_internal/PySide6/Qt/translations"
    if translations.is_dir():
        for path in translations.rglob("*"):
            if path.is_file():
                reclaimed += path.stat().st_size
        _rmtree(translations)

    # Guard against a Qt/PyInstaller layout change (or a reachability bug) that
    # makes the prune drop something load-bearing: the bundle is useless without
    # the core Qt libraries, a platform plugin, and the JPEG codec.
    survivors = {p.name for p in dist.rglob("*") if not p.is_symlink()}
    required = ("libQt6Core.so.6", "libQt6Gui.so.6", "libqoffscreen.so", "libqjpeg.so")
    for must in required:
        if must not in survivors:
            sys.exit(f"error: prune removed a required file ({must}); aborting")

    return reclaimed


def _rmtree(path: Path) -> None:
    for child in sorted(path.rglob("*"), reverse=True):
        child.rmdir() if child.is_dir() else child.unlink()
    path.rmdir()


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    dist = Path(sys.argv[1])
    if not (dist / "squill").exists():
        sys.exit(f"error: {dist} is not a PyInstaller squill dist dir")
    reclaimed = prune(dist)
    print(f"prune_bundle: reclaimed {reclaimed / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
