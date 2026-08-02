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

[[ "$VERSION" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]*$ ]] \
  || { echo "unsafe release version: $VERSION" >&2; exit 2; }
[[ "$ARM_SHA" =~ ^[0-9a-f]{64}$ ]] \
  || { echo "invalid arm64 SHA-256" >&2; exit 2; }
[[ "$INTEL_SHA" =~ ^[0-9a-f]{64}$ ]] \
  || { echo "invalid x86_64 SHA-256" >&2; exit 2; }

render_cask() {
  local output="$1"
  mkdir -p "$(dirname "$output")"
  cat > "$output" <<EOF
cask "shotquill" do
  arch arm: "arm64", intel: "x86_64"

  version "${VERSION}"
  sha256 arm:   "${ARM_SHA}",
         intel: "${INTEL_SHA}"

  url "https://github.com/wardmos/shotquill/releases/download/v#{version}/ShotQuill-#{version}-#{arch}.pkg"
  name "ShotQuill"
  desc "Screenshot and annotation tool"
  homepage "https://github.com/wardmos/shotquill"

  depends_on macos: :ventura

EOF

  cat >> "$output" <<'EOF'
  # Homebrew selects the package's guarded CLI component. This keeps a single
  # pair of links under /usr/local/bin for both installation channels and lets
  # the package preinstall reject unrelated same-name commands. Do not use
  # Homebrew binary artifacts here: their uninstall path removes any symlink
  # at the destination without validating its current target.
  pkg "ShotQuill-#{version}-#{arch}.pkg",
      allow_untrusted: true,
      choices:         [
        {
          "choiceIdentifier" => "choice.cli",
          "choiceAttribute"  => "selected",
          "attributeSetting" => 1,
        },
      ]

  # Quit lets Homebrew reopen a running app after upgrade. Delegate all
  # validated user and system cleanup to the coordinator: broad launchctl and
  # pkgutil directives can remove a same-name replacement or a mixed-PKG link.
  # The system-shell wrapper rejects POSIX- or ACL-writable helper paths before
  # parsing package-owned code that can request administrator authorization.
  uninstall quit:   "com.wardmos.shotquill",
            script: {
              executable: "/usr/bin/env",
              args:       [
                "-i",
                "PATH=/usr/bin:/bin:/usr/sbin:/sbin",
                "/bin/bash",
                "--noprofile",
                "--norc",
                "-c",
                "set -euo pipefail; " \
                'helper="/Library/PrivilegedHelperTools/com.wardmos.shotquill.uninstall"; ' \
                'for path in / /Library /Library/PrivilegedHelperTools "$helper"; do   ' \
                'if [ "$path" = "$helper" ]; then [ -f "$path" ] && [ ! -L "$path" ];   ' \
                'else [ -d "$path" ] && [ ! -L "$path" ]; fi;   ' \
                'owner="$(/usr/bin/stat -f %u "$path")"; [ "$owner" -eq 0 ];   ' \
                'permissions="$(/usr/bin/stat -f %Lp "$path")";   ' \
                'mode=$((8#$permissions)); (( (mode & 8#022) == 0 ));   ' \
                'acl_entry="$(LC_ALL=C /bin/ls -lde "$path" | /usr/bin/sed -n "2p")";   ' \
                '[ -z "$acl_entry" ]; ' \
                "done; " \
                'exec /bin/bash --noprofile --norc "$helper" --cli-coordinator',
              ],
            }
end
EOF
}

if [ -n "${SHOTQUILL_CASK_OUTPUT:-}" ]; then
  render_cask "$SHOTQUILL_CASK_OUTPUT"
  echo "Rendered ShotQuill cask to $SHOTQUILL_CASK_OUTPUT"
  exit 0
fi

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
render_cask "$WORK/Casks/shotquill.rb"

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
