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

pyinstaller --noconfirm --windowed --name ShotQuill \
  --osx-bundle-identifier com.wardmos.shotquill \
  --icon "$ICNS" \
  --paths src \
  packaging/entry.py

# Menu-bar agent app (no Dock icon) + screen-recording prompt text + version.
"$PB" -c "Add :LSUIElement bool true" "$PLIST" 2>/dev/null \
  || "$PB" -c "Set :LSUIElement true" "$PLIST"
"$PB" -c "Add :NSScreenCaptureUsageDescription string 'ShotQuill captures your screen to take screenshots.'" "$PLIST" 2>/dev/null \
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

DMG="dist/ShotQuill-$VERSION.dmg"
hdiutil create -volname "ShotQuill $VERSION" -srcfolder "$STAGING" -ov -format UDZO "$DMG"
rm -rf "$STAGING"

echo "Built $DMG"
