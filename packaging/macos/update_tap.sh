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
URL="https://github.com/wardmos/shotquill/releases/download/v${VERSION}/ShotQuill-${VERSION}.dmg"

WORK="$(mktemp -d)"
# Authenticate with an HTTP header injected via GIT_CONFIG_* env vars rather than
# the command line, so the token never lands in world-readable /proc/PID/cmdline
# and is never written to the clone's .git/config.
AUTH_HEADER="Authorization: Basic $(printf 'x-access-token:%s' "$TAP_TOKEN" | base64 | tr -d '\n')"
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0="http.extraHeader"
export GIT_CONFIG_VALUE_0="$AUTH_HEADER"
git clone --depth 1 "https://github.com/${REPO}.git" "$WORK"
mkdir -p "$WORK/Casks"

cat > "$WORK/Casks/shotquill.rb" <<EOF
cask "shotquill" do
  version "${VERSION}"
  sha256 "${SHA}"

  url "${URL}"
  name "ShotQuill"
  desc "Screenshot and annotation tool"
  homepage "https://github.com/wardmos/shotquill"

  app "ShotQuill.app"

  # Ad-hoc-signed build: drop the quarantine flag so it opens without a warning.
  postflight do
    system_command "/usr/bin/xattr",
                   args: ["-dr", "com.apple.quarantine", "#{appdir}/ShotQuill.app"]
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
