#!/usr/bin/env bash
# Build ShotQuill.app and a drag-to-Applications DMG.
# Ad-hoc signed (anonymous, no Apple account, no notarization).
#
# Usage: packaging/macos/build_dmg.sh <version-or-tag>
set -euo pipefail

VERSION="${1:-0.0.0}"
VERSION="${VERSION#v}"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

APP="dist/ShotQuill.app"
PLIST="$APP/Contents/Info.plist"
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

pyinstaller --noconfirm --windowed --name ShotQuill \
  --osx-bundle-identifier com.wardmos.shotquill \
  --icon "$ICNS" \
  --paths src \
  --strip \
  "${EXCLUDE_FLAGS[@]}" \
  packaging/entry.py

# Menu-bar agent app (no Dock icon) + privacy prompt text + version.
"$PB" -c "Add :LSUIElement bool true" "$PLIST" 2>/dev/null \
  || "$PB" -c "Set :LSUIElement true" "$PLIST"
"$PB" -c "Add :NSScreenCaptureUsageDescription string 'ShotQuill captures your screen to take screenshots.'" "$PLIST" 2>/dev/null \
  || "$PB" -c "Set :NSScreenCaptureUsageDescription 'ShotQuill captures your screen to take screenshots.'" "$PLIST"
"$PB" -c "Add :NSInputMonitoringUsageDescription string 'ShotQuill listens for your configured global screenshot hotkeys.'" "$PLIST" 2>/dev/null \
  || "$PB" -c "Set :NSInputMonitoringUsageDescription 'ShotQuill listens for your configured global screenshot hotkeys.'" "$PLIST"
"$PB" -c "Add :NSAccessibilityUsageDescription string 'ShotQuill may need accessibility access to receive global screenshot hotkeys.'" "$PLIST" 2>/dev/null \
  || "$PB" -c "Set :NSAccessibilityUsageDescription 'ShotQuill may need accessibility access to receive global screenshot hotkeys.'" "$PLIST"
"$PB" -c "Set :CFBundleShortVersionString $VERSION" "$PLIST" 2>/dev/null \
  || "$PB" -c "Add :CFBundleShortVersionString string $VERSION" "$PLIST"
"$PB" -c "Set :CFBundleVersion $VERSION" "$PLIST" 2>/dev/null \
  || "$PB" -c "Add :CFBundleVersion string $VERSION" "$PLIST"

# Ad-hoc signature: required to run on Apple Silicon; embeds no identity.
codesign --force --deep --sign - "$APP"

# Assemble the DMG with hdiutil (built into macOS, no extra dependency).
STAGING="dist/dmg"
rm -rf "$STAGING"
mkdir -p "$STAGING"
cp -R "$APP" "$STAGING/"
ln -s /Applications "$STAGING/Applications"

DMG="dist/ShotQuill-$VERSION.dmg"
# ULMO = LZMA-compressed DMG: noticeably smaller than UDZO (zlib). Mountable on
# macOS 10.15+, which is well below ShotQuill's target.
hdiutil create -volname "ShotQuill $VERSION" -srcfolder "$STAGING" -ov -format ULMO "$DMG"
rm -rf "$STAGING"

echo "Built $DMG"
