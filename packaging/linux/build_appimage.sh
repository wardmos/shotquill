#!/usr/bin/env bash
# Build a single-file ShotQuill AppImage: the CLI/MCP in a box for Linux.
#
# Usage: packaging/linux/build_appimage.sh <version-or-tag>
#
# This AppImage ships only the headless CLI/MCP surface. The menu-bar GUI
# also runs on Linux but is distributed via PyPI / pipx
# instead, so it can pull in QtWidgets and the rest of the UI stack at install
# time. Keeping the AppImage CLI-only keeps the binary small and makes it the
# right shape for scripts and AI agents. The macOS-only app/ui/pyobjc code is
# excluded so a bare run shows `--help`. PyInstaller bundles Python + Qt;
# appimagetool wraps the AppDir into one self-mounting executable.
#
# glibc floor: AppImage does not bundle glibc, so the binary needs a glibc at
# least as new as the build host's. Built on GitHub's ubuntu-22.04 that floor is
# 2.35 (Ubuntu 22.04+ / Debian 12+); an older floor needs an older build base (a
# container), tracked separately in TODO.
set -euo pipefail

VERSION="${1:-0.0.0}"
VERSION="${VERSION#v}"
ARCH="${ARCH:-x86_64}"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

rm -rf build/appimage dist

# The CLI path only needs QtCore / QtGui / QtDBus (the Wayland portal). Drop the
# rest of the Qt stack and the macOS-only GUI/backends/pyobjc so they (and their
# shared libraries) stay out of the bundle. Mirrors the macOS DMG excludes, but
# keeps QtDBus (Wayland) and drops QtWidgets (no GUI here).
EXCLUDES=(
  PySide6.QtWidgets PySide6.QtNetwork
  PySide6.QtWebEngineCore PySide6.QtWebEngineWidgets PySide6.QtWebEngineQuick
  PySide6.QtWebChannel PySide6.QtWebSockets PySide6.QtNetworkAuth PySide6.QtHttpServer
  PySide6.QtQml PySide6.QtQmlModels PySide6.QtQuick PySide6.QtQuick3D
  PySide6.QtQuickWidgets PySide6.QtQuickControls2
  PySide6.Qt3DCore PySide6.Qt3DRender PySide6.Qt3DInput PySide6.Qt3DLogic
  PySide6.Qt3DAnimation PySide6.Qt3DExtras
  PySide6.QtCharts PySide6.QtDataVisualization PySide6.QtGraphs
  PySide6.QtMultimedia PySide6.QtMultimediaWidgets PySide6.QtSpatialAudio
  PySide6.QtPdf PySide6.QtPdfWidgets PySide6.QtSql PySide6.QtSvg PySide6.QtSvgWidgets
  PySide6.QtBluetooth PySide6.QtNfc PySide6.QtPositioning PySide6.QtLocation
  PySide6.QtSensors PySide6.QtSerialPort PySide6.QtSerialBus
  PySide6.QtRemoteObjects PySide6.QtScxml PySide6.QtStateMachine
  PySide6.QtTextToSpeech PySide6.QtHelp PySide6.QtDesigner PySide6.QtUiTools
  PySide6.QtTest PySide6.QtXml
  shotquill.app shotquill.ui
  shotquill.capture.macos shotquill.hotkeys.macos shotquill.autostart.macos
  shotquill.ocr.macos
  Quartz Cocoa AppKit Foundation objc ScreenCaptureKit Vision
  tkinter
)
EXCLUDE_FLAGS=()
for mod in "${EXCLUDES[@]}"; do EXCLUDE_FLAGS+=(--exclude-module "$mod"); done

# --strip drops ELF symbol tables; --noupx keeps UPX off on purpose. UPX-packed
# .so files hide their DT_NEEDED from readelf, which is exactly what
# prune_bundle.py walks to decide what's dead — packing would blind the prune.
# AppImage also squashes the whole tree below, so per-file UPX buys little here.
pyinstaller --noconfirm --name squill \
  --paths src \
  --strip \
  --noupx \
  --optimize 2 \
  --workpath build/appimage/pyinstaller \
  --distpath build/appimage/dist \
  "${EXCLUDE_FLAGS[@]}" \
  packaging/linux/cli_entry.py

# Prune the dead weight --exclude-module can't reach: it drops Python *modules*,
# but PyInstaller's PySide6 hook still copies whole subtrees of shared libraries
# and Qt plugins (the QML/Quick engine, QtPdf, the GTK platform theme and its
# Pango/ATK/Cairo stack, …) that nothing on the CLI/MCP path loads — ~50 MB on a
# 6.11 build. The pruner removes only libraries unreachable from the real entry
# points, so it can't break a kept path; it fails loudly if the keep policy ever
# prunes a required plugin (a Qt-layout-drift guard, like the macOS DMG's).
python3 packaging/linux/prune_bundle.py build/appimage/dist/squill

# Assemble the AppDir: the PyInstaller tree under usr/bin, a .desktop + icon,
# and an AppRun that forwards every argument straight to the CLI.
APPDIR="build/appimage/ShotQuill.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
cp -R build/appimage/dist/squill/. "$APPDIR/usr/bin/"

install -Dm644 packaging/linux/shotquill.desktop "$APPDIR/shotquill.desktop"
install -Dm644 packaging/macos/icon.png "$APPDIR/shotquill.png"
cp "$APPDIR/shotquill.png" "$APPDIR/.DirIcon"

cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/squill" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

# appimagetool, pinned to a stable AppImageKit release. extract-and-run so it
# needs no FUSE in CI/containers.
TOOL="build/appimage/appimagetool"
if [ ! -x "$TOOL" ]; then
  curl -fsSL -o "$TOOL" \
    "https://github.com/AppImage/AppImageKit/releases/download/13/obsolete-appimagetool-x86_64.AppImage"
  chmod +x "$TOOL"
fi

mkdir -p dist
OUTPUT="dist/ShotQuill-$VERSION-$ARCH.AppImage"
# --comp xz: smaller files than the default gzip squashfs. AppImageKit 13's
# appimagetool only offers gzip and xz (no zstd); xz is the classic AppImage
# default and its runtime mounts it, so the glibc floor (set by the build host,
# above) is what still gates launch.
ARCH="$ARCH" APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --comp xz "$APPDIR" "$OUTPUT"
echo "Built $OUTPUT"
