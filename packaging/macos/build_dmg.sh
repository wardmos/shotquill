#!/usr/bin/env bash
# Build Shotquill.app and a drag-to-Applications DMG.
# Ad-hoc signed (anonymous, no Apple account, no notarization).
#
# Usage: packaging/macos/build_dmg.sh <version-or-tag>
set -euo pipefail

VERSION="${1:-0.0.0}"
VERSION="${VERSION#v}"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

APP="dist/Shotquill.app"
PLIST="$APP/Contents/Info.plist"
PB="/usr/libexec/PlistBuddy"

rm -rf build dist

pyinstaller --noconfirm --windowed --name Shotquill \
  --osx-bundle-identifier com.wardmos.shotquill \
  --paths src \
  packaging/entry.py

# Menu-bar agent app (no Dock icon) + screen-recording prompt text + version.
"$PB" -c "Add :LSUIElement bool true" "$PLIST" 2>/dev/null \
  || "$PB" -c "Set :LSUIElement true" "$PLIST"
"$PB" -c "Add :NSScreenCaptureUsageDescription string 'Shotquill captures your screen to take screenshots.'" "$PLIST" 2>/dev/null \
  || true
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

DMG="dist/Shotquill-$VERSION.dmg"
hdiutil create -volname "Shotquill $VERSION" -srcfolder "$STAGING" -ov -format UDZO "$DMG"
rm -rf "$STAGING"

echo "Built $DMG"
