#!/usr/bin/env bash
# Build ShotQuill.app and an installer PKG, one per CPU architecture.
# The app is ad-hoc signed by default. Set SHOTQUILL_INSTALLER_IDENTITY to sign
# the outer product archive with a Developer ID Installer identity.
#
# Usage: packaging/macos/build_pkg.sh <version-or-tag> [arch ...]
#   arch: arm64 | x86_64 | universal2 (default: all three)
#
# SHOTQUILL_BUILD_VERSION optionally supplies a monotonically increasing,
# numeric CFBundleVersion and component-package version. CI sets it to the
# repository-wide GitHub run ID so a PKG can upgrade an App installed by Brew
# even when both builds have the same user-facing product version.
#
# Single-arch PKGs are roughly half the size of universal2 because PyInstaller
# thins the fat (universal2) Python/Qt binaries down to one slice. Building a
# non-native or universal2 app requires a universal2 Python and universal2
# wheels for every binary dependency; PyInstaller fails loudly if that does not
# hold. universal2 is therefore the strictest arch: the package smoke workflow
# builds only it on PRs, since the thinned arm64/x86_64 slices cannot fail
# independently of it.
set -euo pipefail

VERSION="${1:-0.0.0}"
VERSION="${VERSION#v}"
BUILD_VERSION="${SHOTQUILL_BUILD_VERSION:-$VERSION}"
shift || true
ARCHES=("$@")
if [ ${#ARCHES[@]} -eq 0 ]; then
  ARCHES=(arm64 x86_64 universal2)
fi
if [[ ! "$VERSION" =~ ^[0-9]+([.][0-9]+){0,2}$ ]]; then
  echo "error: unsafe package version: $VERSION" >&2
  exit 2
fi
if [[ ! "$BUILD_VERSION" =~ ^[0-9]+([.][0-9]+){0,2}$ ]]; then
  echo "error: unsafe package build version: $BUILD_VERSION" >&2
  exit 2
fi
for arch in "${ARCHES[@]}"; do
  case "$arch" in
    arm64|x86_64|universal2) ;;
    *)
      echo "error: unsupported architecture: $arch" >&2
      exit 2
      ;;
  esac
done

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

PB="/usr/libexec/PlistBuddy"
MACOS_MIN_VERSION="13.0"

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

  # --noupx: UPX rewrites Mach-O headers, which breaks the ad-hoc codesign below
  # and Apple Silicon's loader. macOS runners ship no upx anyway, but keeping
  # this explicit avoids architecture-specific bundle failures.
  MACOSX_DEPLOYMENT_TARGET="$MACOS_MIN_VERSION" \
  pyinstaller --noconfirm --windowed --name ShotQuill \
    --osx-bundle-identifier com.wardmos.shotquill \
    --icon "$ICNS" \
    --paths src \
    --strip \
    --noupx \
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
  "$PB" -c "Set :CFBundleShortVersionString $VERSION" "$plist" 2>/dev/null \
    || "$PB" -c "Add :CFBundleShortVersionString string $VERSION" "$plist"
  "$PB" -c "Set :CFBundleVersion $BUILD_VERSION" "$plist" 2>/dev/null \
    || "$PB" -c "Add :CFBundleVersion string $BUILD_VERSION" "$plist"
  "$PB" -c "Set :LSMinimumSystemVersion $MACOS_MIN_VERSION" "$plist" 2>/dev/null \
    || "$PB" -c "Add :LSMinimumSystemVersion string $MACOS_MIN_VERSION" "$plist"

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

  # Build three component packages. The application and fixed-target uninstall
  # helper are required; Distribution.xml exposes the two CLI links as an
  # optional, default-on choice. The helper is not a daemon. Its protected,
  # root-owned location prevents replacement while macOS is authorizing a
  # one-shot uninstall. Keeping the CLI root flat avoids changing ownership of
  # an existing /usr/local tree (including Intel Homebrew installations).
  local package_work="build/$arch/product"
  local component_dir="$package_work/components"
  local app_root="$package_work/app-root"
  local cli_root="$package_work/cli-root"
  local uninstaller_root="$package_work/uninstaller-root"
  local cli_script_source="packaging/macos/scripts/cli"
  local cli_scripts="$package_work/cli-scripts"
  local cli_arch_flags=()
  local app_component="ShotQuill-app.pkg"
  local cli_component="ShotQuill-cli.pkg"
  local uninstaller_component="ShotQuill-uninstaller.pkg"
  local distribution="$package_work/Distribution.xml"
  rm -rf "$package_work"
  mkdir -p \
    "$component_dir" \
    "$app_root" \
    "$cli_root" \
    "$uninstaller_root" \
    "$cli_scripts"
  ditto "$app" "$app_root/ShotQuill.app"
  install -m 0755 "$cli_script_source/preinstall" "$cli_scripts/preinstall"
  if [ "$arch" = universal2 ]; then
    cli_arch_flags=(-arch arm64 -arch x86_64)
  else
    cli_arch_flags=(-arch "$arch")
  fi
  xcrun clang -std=c11 -Os -Wall -Wextra -Werror \
    -mmacosx-version-min="$MACOS_MIN_VERSION" \
    "${cli_arch_flags[@]}" \
    packaging/macos/cli_link_installer.c -o "$cli_scripts/postinstall"
  chmod 0755 "$cli_scripts/postinstall"
  codesign --force --sign - "$cli_scripts/postinstall"
  install -m 0755 packaging/macos/uninstall_pkg \
    "$uninstaller_root/com.wardmos.shotquill.uninstall"

  pkgbuild \
    --root "$app_root" \
    --install-location /Applications \
    --component-plist packaging/macos/app_components.plist \
    --identifier com.wardmos.shotquill.app \
    --version "$BUILD_VERSION" \
    --ownership recommended \
    "$component_dir/$app_component"
  # A true payload-free package runs scripts but intentionally leaves no
  # receipt. An empty payload root keeps the CLI optional while giving upgrades
  # and the guarded uninstaller a receipt to track and forget.
  pkgbuild \
    --root "$cli_root" \
    --install-location / \
    --scripts "$cli_scripts" \
    --identifier com.wardmos.shotquill.cli \
    --version "$BUILD_VERSION" \
    "$component_dir/$cli_component"
  pkgbuild \
    --root "$uninstaller_root" \
    --install-location /Library/PrivilegedHelperTools \
    --identifier com.wardmos.shotquill.uninstaller \
    --version "$BUILD_VERSION" \
    --ownership recommended \
    "$component_dir/$uninstaller_component"

  python packaging/macos/pkg_distribution.py \
    --version "$BUILD_VERSION" \
    --app-package "$app_component" \
    --cli-package "$cli_component" \
    --uninstaller-package "$uninstaller_component" \
    > "$distribution"

  local pkg="dist/ShotQuill-$VERSION-$arch.pkg"
  local productbuild_args=(
    --distribution "$distribution"
    --package-path "$component_dir"
  )
  if [ -n "${SHOTQUILL_INSTALLER_IDENTITY:-}" ]; then
    productbuild_args+=(--sign "$SHOTQUILL_INSTALLER_IDENTITY")
  fi
  productbuild "${productbuild_args[@]}" "$pkg"

  # Parse the finished package once so malformed Distribution XML cannot become
  # a release artifact even if productbuild accepted it.
  installer -showChoicesXML -pkg "$pkg" -target / >/dev/null
  echo "Built $pkg"
}

for arch in "${ARCHES[@]}"; do
  build_one "$arch"
done
