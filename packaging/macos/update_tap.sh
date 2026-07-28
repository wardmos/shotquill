#!/usr/bin/env bash
# Regenerate the Homebrew cask in wardmos/homebrew-tap after a release.
# No-ops (exit 0) when TAP_TOKEN is unset, so releases still succeed without it.
#
# Usage: packaging/macos/update_tap.sh <tag> <arm64-sha256> <x86_64-sha256>
set -euo pipefail

USAGE="usage: update_tap.sh <tag> <arm64-sha256> <x86_64-sha256>"
VERSION="${1:?$USAGE}"
VERSION="${VERSION#v}"
ARM_SHA="${2:?$USAGE}"
INTEL_SHA="${3:?$USAGE}"

if [ -z "${TAP_TOKEN:-}" ]; then
  echo "TAP_TOKEN not set; skipping Homebrew tap update."
  exit 0
fi

REPO="wardmos/homebrew-tap"

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
  arch arm: "arm64", intel: "x86_64"

  version "${VERSION}"
  sha256 arm:   "${ARM_SHA}",
         intel: "${INTEL_SHA}"

  url "https://github.com/wardmos/shotquill/releases/download/v#{version}/ShotQuill-#{version}-#{arch}.pkg"
  name "ShotQuill"
  desc "Screenshot and annotation tool"
  homepage "https://github.com/wardmos/shotquill"

  # The direct installer offers CLI links as an optional component. Homebrew
  # leaves that component disabled and owns links in its own prefix instead,
  # avoiding collisions with Intel Homebrew's /usr/local/bin.
  pkg "ShotQuill-#{version}-#{arch}.pkg",
      allow_untrusted: true,
      choices: [
        {
          "choiceIdentifier" => "choice.cli",
          "choiceAttribute"  => "selected",
          "attributeSetting" => 0,
        },
      ]

  # The bundled binary doubles as the CLI (bare invocation opens the GUI),
  # so link it onto PATH under both documented command names.
  binary "/Applications/ShotQuill.app/Contents/MacOS/ShotQuill", target: "shotquill"
  binary "/Applications/ShotQuill.app/Contents/MacOS/ShotQuill", target: "squill"

  # The app is ad-hoc signed until release signing is configured.
  postflight do
    system_command "/usr/bin/xattr",
                   args: ["-dr", "com.apple.quarantine", "/Applications/ShotQuill.app"],
                   sudo: true
  end

  uninstall quit:    "com.wardmos.shotquill",
            pkgutil: "com.wardmos.shotquill.app"
end
EOF

cd "$WORK"
git add Casks/shotquill.rb
if git diff --cached --quiet; then
  echo "${REPO} cask is already at ${VERSION}"
  exit 0
fi
git -c user.name="shotquill-release" \
    -c user.email="release@users.noreply.github.com" \
    commit -m "shotquill ${VERSION}"
git push
echo "Updated ${REPO} cask to ${VERSION}"
