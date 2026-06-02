#!/usr/bin/env bash
# Regenerate the Homebrew cask in wardmos/homebrew-tap after a release.
# No-ops (exit 0) when TAP_TOKEN is unset, so releases still succeed without it.
#
# Usage: packaging/macos/update_tap.sh <tag> <sha256>
set -euo pipefail

VERSION="${1:?usage: update_tap.sh <tag> <sha256>}"
VERSION="${VERSION#v}"
SHA="${2:?usage: update_tap.sh <tag> <sha256>}"

if [ -z "${TAP_TOKEN:-}" ]; then
  echo "TAP_TOKEN not set; skipping Homebrew tap update."
  exit 0
fi

REPO="wardmos/homebrew-tap"
URL="https://github.com/wardmos/shotquill/releases/download/v${VERSION}/Shotquill-${VERSION}.dmg"

WORK="$(mktemp -d)"
# Authenticate with an HTTP header passed per-command, never embedded in the
# remote URL, so the token is never written to the clone's .git/config.
AUTH_HEADER="Authorization: Basic $(printf 'x-access-token:%s' "$TAP_TOKEN" | base64 | tr -d '\n')"
git -c http.extraHeader="$AUTH_HEADER" clone --depth 1 "https://github.com/${REPO}.git" "$WORK"
mkdir -p "$WORK/Casks"

cat > "$WORK/Casks/shotquill.rb" <<EOF
cask "shotquill" do
  version "${VERSION}"
  sha256 "${SHA}"

  url "${URL}"
  name "Shotquill"
  desc "Screenshot and annotation tool"
  homepage "https://github.com/wardmos/shotquill"

  app "Shotquill.app"

  # Ad-hoc-signed build: drop the quarantine flag so it opens without a warning.
  postflight do
    system_command "/usr/bin/xattr",
                   args: ["-dr", "com.apple.quarantine", "#{appdir}/Shotquill.app"]
  end
end
EOF

cd "$WORK"
git add Casks/shotquill.rb
git -c user.name="shotquill-release" \
    -c user.email="release@users.noreply.github.com" \
    commit -m "shotquill ${VERSION}"
git -c http.extraHeader="$AUTH_HEADER" push
echo "Updated ${REPO} cask to ${VERSION}"
