#!/usr/bin/env bash
# Build ShotQuill.app and drag-to-Applications DMGs, one per CPU architecture.
# Ad-hoc signed (anonymous, no Apple account, no notarization).
#
# Usage: packaging/macos/build_dmg.sh <version-or-tag> [arch ...]
#   arch: arm64 | x86_64 | universal2 (default: all three)
#
# Single-arch DMGs are roughly half the size of universal2 because PyInstaller
# thins the fat (universal2) Python/Qt binaries down to one slice. Building a
# non-native or universal2 app requires a universal2 Python and universal2
# wheels for every binary dependency; PyInstaller fails loudly if that does not
# hold. universal2 is therefore the strictest arch: the package smoke workflow
# builds only it on PRs, since the thinned arm64/x86_64 slices cannot fail
# independently of it.
#
# SHOTQUILL_DMG_FORMAT overrides the DMG compression (default ULMO = LZMA,
# smallest but slow); smoke builds use UDZO (zlib) since their DMG is a
# short-lived artifact where speed matters more than size.
set -euo pipefail

VERSION="${1:-0.0.0}"
VERSION="${VERSION#v}"
shift || true
ARCHES=("$@")
if [ ${#ARCHES[@]} -eq 0 ]; then
  ARCHES=(arm64 x86_64 universal2)
fi

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

PB="/usr/libexec/PlistBuddy"

rm -rf build dist

# Build ShotQuill.icns from the committed master PNG so the Launchpad / Finder
# icon matches the menu-bar mark (sips + iconutil are built into macOS).
ICON_PNG="packaging/macos/icon.png"
ICONSET="build/ShotQuill.iconset"
ICNS="build/ShotQuill.icns"
mkdir -p "$ICONSET"
for spec in "16:16x16" "32:16x16@2x" "32:32x32" "64:32x32@2x" \
            "128:128x128" "256:128x128@2x" "256:256x256" "512:256x256@2x" \
            "512:512x512" "1024:512x512@2x"; do
  px="${spec%%:*}"
  name="${spec##*:}"
  sips -z "$px" "$px" "$ICON_PNG" --out "$ICONSET/icon_$name.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$ICNS"

# ShotQuill only uses QtCore / QtGui / QtWidgets, but PyInstaller's PySide6 hook
# would otherwise pull in the entire Qt stack (Chromium/WebEngine, QML, Qt3D,
# Charts, Multimedia, …) — hundreds of MB the app never touches. Drop the unused
# Qt modules so they (and their shared libraries) stay out of the bundle.
QT_EXCLUDES=(
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
  PySide6.QtTest PySide6.QtXml PySide6.QtDBus PySide6.QtNetwork
  tkinter
)
EXCLUDE_FLAGS=()
for mod in "${QT_EXCLUDES[@]}"; do
  EXCLUDE_FLAGS+=(--exclude-module "$mod")
done

build_one() {
  local arch="$1"
  local app="dist/$arch/ShotQuill.app"
  local plist="$app/Contents/Info.plist"

  pyinstaller --noconfirm --windowed --name ShotQuill \
    --osx-bundle-identifier com.wardmos.shotquill \
    --icon "$ICNS" \
    --paths src \
    --strip \
    --optimize 2 \
    --target-arch "$arch" \
    --workpath "build/$arch" \
    --distpath "dist/$arch" \
    "${EXCLUDE_FLAGS[@]}" \
    packaging/entry.py

  # Menu-bar agent app (no Dock icon) + privacy prompt text + version.
  "$PB" -c "Add :LSUIElement bool true" "$plist" 2>/dev/null \
    || "$PB" -c "Set :LSUIElement true" "$plist"
  "$PB" -c "Add :NSScreenCaptureUsageDescription string 'ShotQuill captures your screen to take screenshots.'" "$plist" 2>/dev/null \
    || "$PB" -c "Set :NSScreenCaptureUsageDescription 'ShotQuill captures your screen to take screenshots.'" "$plist"
  "$PB" -c "Add :NSInputMonitoringUsageDescription string 'ShotQuill listens for your configured global screenshot hotkeys.'" "$plist" 2>/dev/null \
    || "$PB" -c "Set :NSInputMonitoringUsageDescription 'ShotQuill listens for your configured global screenshot hotkeys.'" "$plist"
  "$PB" -c "Add :NSAccessibilityUsageDescription string 'ShotQuill may need accessibility access to receive global screenshot hotkeys.'" "$plist" 2>/dev/null \
    || "$PB" -c "Set :NSAccessibilityUsageDescription 'ShotQuill may need accessibility access to receive global screenshot hotkeys.'" "$plist"
  "$PB" -c "Set :CFBundleShortVersionString $VERSION" "$plist" 2>/dev/null \
    || "$PB" -c "Add :CFBundleShortVersionString string $VERSION" "$plist"
  "$PB" -c "Set :CFBundleVersion $VERSION" "$plist" 2>/dev/null \
    || "$PB" -c "Add :CFBundleVersion string $VERSION" "$plist"

  # Trim payload --exclude-module cannot reach (it works per-module, not
  # per-file). Prune both Contents/Frameworks and Contents/Resources:
  # PyInstaller 6 mirrors each Qt tree into the other via symlinks, and a
  # dangling leftover half would break the codesign below.
  #   - translations: the UI is English-only.
  #   - imageformats plugins: ShotQuill only writes PNG (built into QtGui)
  #     and JPEG (libqjpeg); tiff/webp/gif/... are dead weight.
  local qtdir
  for qtdir in "$app/Contents/Frameworks/PySide6/Qt" "$app/Contents/Resources/PySide6/Qt"; do
    rm -rf "$qtdir/translations"
    if [ -d "$qtdir/plugins/imageformats" ]; then
      find "$qtdir/plugins/imageformats" \( -type f -o -type l \) ! -name "libqjpeg*" -delete
    fi
  done
  # Layout-drift guard: if PyInstaller relocates the plugins the prune above
  # silently no-ops, and a later Qt/PyInstaller bump could also drop JPEG
  # support unnoticed. Fail the build instead.
  find "$app" -name "libqjpeg*" -type f | grep -q . \
    || { echo "error: JPEG imageformat plugin missing after prune" >&2; exit 1; }

  # Ad-hoc signature: required to run on Apple Silicon; embeds no identity.
  codesign --force --deep --sign - "$app"

  # Assemble the DMG with hdiutil (built into macOS, no extra dependency).
  local staging="dist/$arch/dmg"
  rm -rf "$staging"
  mkdir -p "$staging"
  cp -R "$app" "$staging/"
  ln -s /Applications "$staging/Applications"

  local dmg="dist/ShotQuill-$VERSION-$arch.dmg"
  # ULMO = LZMA-compressed DMG: noticeably smaller than UDZO (zlib). Mountable on
  # macOS 10.15+, which is well below ShotQuill's target.
  hdiutil create -volname "ShotQuill $VERSION" -srcfolder "$staging" -ov \
    -format "${SHOTQUILL_DMG_FORMAT:-ULMO}" "$dmg"
  rm -rf "$staging"

  echo "Built $dmg"
}

for arch in "${ARCHES[@]}"; do
  build_one "$arch"
done
