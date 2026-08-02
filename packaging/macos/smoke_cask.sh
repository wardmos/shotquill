#!/usr/bin/env bash
# Render and load the exact Cask that the release job would push to the tap.
set -Eeuo pipefail
umask 077

USAGE="usage: smoke_cask.sh <tag> <arm64-sha256> <x86_64-sha256>"
[ "$#" -eq 3 ] || { echo "$USAGE" >&2; exit 2; }

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TAP_NAME="shotquill/shotquill-ci-$$"
TAP_CREATED=0

cleanup() {
  local status=$?
  trap - EXIT
  set +e
  if [ "$TAP_CREATED" -eq 1 ]; then
    brew untap "$TAP_NAME" >/dev/null 2>&1
  fi
  exit "$status"
}
trap cleanup EXIT

brew tap-new --no-git "$TAP_NAME" >/dev/null
TAP_CREATED=1
TAP_DIR="$(brew --repository "$TAP_NAME")"
CASK="$TAP_DIR/Casks/shotquill.rb"

SHOTQUILL_CASK_OUTPUT="$CASK" \
  bash "$ROOT/packaging/macos/update_tap.sh" "$1" "$2" "$3"

brew ruby -- -c "$CASK"
brew readall --os=all --arch=all "$TAP_NAME"
brew style --cask "$CASK"
