#!/usr/bin/env bash
# Exercise the release-grade universal macOS PKG on an ephemeral CI runner.
set -Eeuo pipefail
trap 'status=$?; echo "smoke_pkg.sh: command failed at line $LINENO: $BASH_COMMAND (exit $status)" >&2' ERR
shopt -s nullglob
umask 077

USAGE="usage: smoke_pkg.sh <universal2.pkg>"
PKG="${1:?$USAGE}"
[ "$#" -eq 1 ] || { echo "$USAGE" >&2; exit 2; }
[ -f "$PKG" ] || { echo "PKG not found: $PKG" >&2; exit 2; }

EXPECTED_TARGET="/Applications/ShotQuill.app/Contents/MacOS/ShotQuill"
SMOKE_ROOT="$(/usr/bin/mktemp -d "${TMPDIR:-/private/tmp}/shotquill-pkg-smoke.XXXXXX")"
EXPANDED="$SMOKE_ROOT/expanded"
ROOT_STAGE=""
SHOTQUILL_PROBE_IDENTITY=""
SHOTQUILL_PROBE_TARGET=""
SQUILL_PROBE_IDENTITY=""
SQUILL_PROBE_TARGET=""

clear_cli_probe() {
  case "$1" in
    shotquill)
      SHOTQUILL_PROBE_IDENTITY=""
      SHOTQUILL_PROBE_TARGET=""
      ;;
    squill)
      SQUILL_PROBE_IDENTITY=""
      SQUILL_PROBE_TARGET=""
      ;;
    *) return 2 ;;
  esac
}

track_cli_probe() {
  local command="$1"
  local expected_target="$2"
  local path="/usr/local/bin/$command"
  local identity actual_target
  [ -L "$path" ] || return 1
  actual_target="$(/usr/bin/readlink -n "$path")" || return 1
  [ "$actual_target" = "$expected_target" ] || return 1
  identity="$(/usr/bin/stat -f '%d:%i' "$path")" || return 1
  case "$command" in
    shotquill)
      SHOTQUILL_PROBE_IDENTITY="$identity"
      SHOTQUILL_PROBE_TARGET="$expected_target"
      ;;
    squill)
      SQUILL_PROBE_IDENTITY="$identity"
      SQUILL_PROBE_TARGET="$expected_target"
      ;;
    *) return 2 ;;
  esac
}

remove_cli_probe() {
  local command="$1"
  local expected_identity expected_target path staged staged_identity staged_target
  case "$command" in
    shotquill)
      expected_identity="$SHOTQUILL_PROBE_IDENTITY"
      expected_target="$SHOTQUILL_PROBE_TARGET"
      ;;
    squill)
      expected_identity="$SQUILL_PROBE_IDENTITY"
      expected_target="$SQUILL_PROBE_TARGET"
      ;;
    *) return 2 ;;
  esac
  [ -n "$expected_identity" ] || return 0
  path="/usr/local/bin/$command"
  if [ ! -e "$path" ] && [ ! -L "$path" ]; then
    clear_cli_probe "$command"
    return 0
  fi
  [ -n "$ROOT_STAGE" ] || return 1
  staged="$ROOT_STAGE/$command.link"
  if /usr/bin/sudo /bin/test -e "$staged" \
    || /usr/bin/sudo /bin/test -L "$staged"; then
    echo "refusing to replace occupied smoke staging path: $staged" >&2
    return 1
  fi
  /usr/bin/sudo /bin/mv -n "$path" "$staged" || return 1
  if ! /usr/bin/sudo /bin/test -L "$staged"; then
    if [ ! -e "$path" ] && [ ! -L "$path" ]; then
      /usr/bin/sudo /bin/mv -n "$staged" "$path" 2>/dev/null || true
    fi
    clear_cli_probe "$command"
    echo "preserved changed CLI probe for $command" >&2
    return 1
  fi
  staged_identity="$(/usr/bin/sudo /usr/bin/stat -f '%d:%i' "$staged")" || return 1
  staged_target="$(/usr/bin/sudo /usr/bin/readlink -n "$staged")" || return 1
  if [ "$staged_identity" = "$expected_identity" ] \
    && [ "$staged_target" = "$expected_target" ]; then
    /usr/bin/sudo /bin/rm "$staged" || return 1
    clear_cli_probe "$command"
    return 0
  fi
  if [ ! -e "$path" ] && [ ! -L "$path" ]; then
    /usr/bin/sudo /bin/mv -n "$staged" "$path" 2>/dev/null || true
  fi
  clear_cli_probe "$command"
  echo "preserved changed CLI probe for $command" >&2
  return 1
}

cleanup_cli_probes() {
  local failed=0
  remove_cli_probe shotquill || failed=1
  remove_cli_probe squill || failed=1
  return "$failed"
}

cleanup() {
  local status=$?
  trap - EXIT
  set +e
  if ! cleanup_cli_probes && [ "$status" -eq 0 ]; then
    status=1
  fi
  if [ -n "$ROOT_STAGE" ] \
    && ! /usr/bin/sudo /bin/rmdir "$ROOT_STAGE" 2>/dev/null \
    && [ "$status" -eq 0 ]; then
    status=1
  fi
  /bin/rm -rf "$SMOKE_ROOT"
  exit "$status"
}
trap cleanup EXIT

/usr/sbin/pkgutil --expand-full "$PKG" "$EXPANDED"
test -f "$EXPANDED/Distribution"
APP_EXECUTABLE_COUNT="$({
  /usr/bin/find "$EXPANDED" \
    -path "*/ShotQuill.app/Contents/MacOS/ShotQuill" -type f -print
} | /usr/bin/wc -l | /usr/bin/tr -d '[:space:]')"
test "$APP_EXECUTABLE_COUNT" = "1"

UNINSTALL_HELPER_COUNT="$({
  /usr/bin/find "$EXPANDED" -type f \
    -name "com.wardmos.shotquill.uninstall" -print
} | /usr/bin/wc -l | /usr/bin/tr -d '[:space:]')"
test "$UNINSTALL_HELPER_COUNT" = "1"
UNINSTALL_HELPER="$(/usr/bin/find "$EXPANDED" -type f \
  -name "com.wardmos.shotquill.uninstall" -print)"
test "$(/usr/bin/stat -f '%Lp' "$UNINSTALL_HELPER")" = "755"
/bin/bash -n "$UNINSTALL_HELPER"
/usr/bin/grep -q "com.wardmos.shotquill.uninstaller" "$EXPANDED/Distribution"

CLI_POSTINSTALL_COUNT="$({
  /usr/bin/find "$EXPANDED" -type f -name postinstall -print
} | /usr/bin/wc -l | /usr/bin/tr -d '[:space:]')"
test "$CLI_POSTINSTALL_COUNT" = "1"
CLI_POSTINSTALL="$(/usr/bin/find "$EXPANDED" -type f -name postinstall -print)"
test -x "$CLI_POSTINSTALL"
/usr/bin/file "$CLI_POSTINSTALL" | /usr/bin/grep -q 'Mach-O universal binary'
/usr/bin/lipo "$CLI_POSTINSTALL" -verify_arch arm64 x86_64
/usr/bin/codesign --verify --strict --all-architectures "$CLI_POSTINSTALL"
for helper_arch in arm64 x86_64; do
  /usr/bin/xcrun vtool -arch "$helper_arch" -show-build "$CLI_POSTINSTALL" \
    | /usr/bin/grep -Eq 'minos[[:space:]]+13[.]0'
done

CURRENT_UID="$(/usr/bin/id -u)"
CURRENT_USER="$(/usr/bin/id -un)"
HOME_RECORD="$(/usr/bin/dscl . -read "/Users/$CURRENT_USER" NFSHomeDirectory)"
case "$HOME_RECORD" in
  "NFSHomeDirectory: "*) USER_HOME="${HOME_RECORD#NFSHomeDirectory: }" ;;
  *) echo "cannot determine the current macOS home directory" >&2; exit 1 ;;
esac
[ -d "$USER_HOME" ] && [ ! -L "$USER_HOME" ] \
  && [ "$(/usr/bin/stat -f '%u' "$USER_HOME")" = "$CURRENT_UID" ] \
  || { echo "current macOS home directory is not private to this user" >&2; exit 1; }

assert_no_existing_install() {
  local path receipt
  for path in \
    /Applications/ShotQuill.app \
    /Library/PrivilegedHelperTools/com.wardmos.shotquill.uninstall \
    /opt/homebrew/Caskroom/shotquill \
    /usr/local/Caskroom/shotquill \
    /usr/local/bin/shotquill \
    /usr/local/bin/squill \
    "$USER_HOME/Library/LaunchAgents/com.wardmos.shotquill.plist"; do
    if [ -e "$path" ] || [ -L "$path" ]; then
      echo "refusing to run over an existing ShotQuill installation object: $path" >&2
      return 1
    fi
  done
  for receipt in \
    com.wardmos.shotquill.app \
    com.wardmos.shotquill.cli \
    com.wardmos.shotquill.uninstaller; do
    if /usr/sbin/pkgutil --pkg-info "$receipt" >/dev/null 2>&1; then
      echo "refusing to forget an existing package receipt: $receipt" >&2
      return 1
    fi
  done
}

assert_no_existing_install

/usr/bin/sudo /bin/mkdir -p /usr/local/bin
ROOT_STAGE="$(
  /usr/bin/sudo /usr/bin/mktemp -d /private/tmp/.shotquill-pkg-smoke-root.XXXXXX
)"
/usr/bin/sudo /bin/test -d "$ROOT_STAGE"
! /usr/bin/sudo /bin/test -L "$ROOT_STAGE"
test "$(/usr/bin/sudo /usr/bin/stat -f '%u:%Lp' "$ROOT_STAGE")" = "0:700"
ROOT_STAGE_ACL="$({
  /usr/bin/sudo /bin/ls -lde "$ROOT_STAGE"
} | /usr/bin/sed -n '2p')"
test -z "$ROOT_STAGE_ACL"
test "$(/usr/bin/sudo /usr/bin/stat -f '%d' "$ROOT_STAGE")" \
  = "$(/usr/bin/stat -f '%d' /usr/local/bin)"

for command in shotquill squill; do
  path="/usr/local/bin/$command"
  test ! -e "$path" && test ! -L "$path"
done

/usr/bin/sudo /bin/ln -s /another/tool /usr/local/bin/squill
track_cli_probe squill /another/tool
if /usr/bin/sudo "$CLI_POSTINSTALL" component.pkg / /; then
  echo "CLI postinstall replaced a conflicting command" >&2
  exit 1
fi
test ! -e /usr/local/bin/shotquill && test ! -L /usr/local/bin/shotquill
test "$(/usr/bin/readlink -n /usr/local/bin/squill)" = /another/tool
CLI_STAGE_RESIDUE=(/private/tmp/.shotquill-cli-install.*)
test "${#CLI_STAGE_RESIDUE[@]}" -eq 0
remove_cli_probe squill

if "$CLI_POSTINSTALL" component.pkg / /; then
  echo "CLI postinstall ran without Installer privileges" >&2
  exit 1
fi
if /usr/bin/sudo "$CLI_POSTINSTALL" component.pkg / /tmp; then
  echo "CLI postinstall accepted a non-system target" >&2
  exit 1
fi

/usr/bin/sudo "$CLI_POSTINSTALL" component.pkg / /
track_cli_probe shotquill "$EXPECTED_TARGET"
track_cli_probe squill "$EXPECTED_TARGET"
SHOTQUILL_IDENTITY="$SHOTQUILL_PROBE_IDENTITY"
SQUILL_IDENTITY="$SQUILL_PROBE_IDENTITY"
/usr/bin/sudo "$CLI_POSTINSTALL" component.pkg / /
test "$(/usr/bin/stat -f '%d:%i' /usr/local/bin/shotquill)" = "$SHOTQUILL_IDENTITY"
test "$(/usr/bin/stat -f '%d:%i' /usr/local/bin/squill)" = "$SQUILL_IDENTITY"
CLI_STAGE_RESIDUE=(/private/tmp/.shotquill-cli-install.*)
test "${#CLI_STAGE_RESIDUE[@]}" -eq 0
cleanup_cli_probes
assert_no_existing_install

/usr/bin/sudo /usr/sbin/installer -pkg "$PKG" -target /

INSTALLED_HELPER="/Library/PrivilegedHelperTools/com.wardmos.shotquill.uninstall"
test -d /Applications/ShotQuill.app
test -x "$INSTALLED_HELPER"
test "$(/usr/bin/stat -f '%u:%Lp' "$INSTALLED_HELPER")" = "0:755"
for receipt in \
  com.wardmos.shotquill.app \
  com.wardmos.shotquill.cli \
  com.wardmos.shotquill.uninstaller; do
  /usr/sbin/pkgutil --pkg-info "$receipt" >/dev/null
done
for command in shotquill squill; do
  test -L "/usr/local/bin/$command"
  test "$(/usr/bin/readlink -n "/usr/local/bin/$command")" = "$EXPECTED_TARGET"
  track_cli_probe "$command" "$EXPECTED_TARGET"
done

# A command installed after the PKG must survive uninstall even while the old
# CLI receipt still lists that path in its BOM.
remove_cli_probe shotquill
/usr/bin/sudo /bin/ln -s /another/tool /usr/local/bin/shotquill
track_cli_probe shotquill /another/tool
/usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin \
  /bin/bash --noprofile --norc "$INSTALLED_HELPER" --cli-coordinator

test ! -e /Applications/ShotQuill.app
test ! -e "$INSTALLED_HELPER"
test -L /usr/local/bin/shotquill
test "$(/usr/bin/readlink -n /usr/local/bin/shotquill)" = /another/tool
test ! -e /usr/local/bin/squill && test ! -L /usr/local/bin/squill
clear_cli_probe squill
for receipt in \
  com.wardmos.shotquill.app \
  com.wardmos.shotquill.cli \
  com.wardmos.shotquill.uninstaller; do
  ! /usr/sbin/pkgutil --pkg-info "$receipt" >/dev/null 2>&1
done
remove_cli_probe shotquill
