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
git clone --depth 1 "https://x-access-token:${TAP_TOKEN}@github.com/${REPO}.git" "$WORK"
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
git push
echo "Updated ${REPO} cask to ${VERSION}"
