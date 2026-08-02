# Packaging

These commands are for local packaging smoke builds. GitHub Actions remains the
source of truth for release artifacts.

Do not copy workflow steps from `.github/workflows/` and run them as local
scripts. The workflows also configure hosted runners, caches, artifacts, release
uploads, and secrets. Locally, invoke the platform scripts under `packaging/`
directly.

Use a clean virtual environment when possible. Packaging dependencies are pinned
by `packaging/constraints-release.txt` so local smoke builds stay close to CI and
release builds.

## macOS PKG

Run on macOS. The script uses macOS tools such as `sips`, `iconutil`,
`codesign`, `pkgbuild`, `productbuild`, `pkgutil`, and `installer`.

```bash
python -m pip install -U pip
python -m pip install -c packaging/constraints-release.txt -e . pyinstaller
bash packaging/macos/build_pkg.sh 0.0.0 arm64
```

The final argument selects the architecture. Use `arm64` on Apple Silicon,
`x86_64` on Intel Macs, or `universal2` to validate the universal build path.
Building non-native or universal2 bundles requires a universal2 Python and
compatible universal2 wheels. Release dependencies currently set macOS 13 as
the deployment baseline; the app metadata and every native CLI-installer slice
are built and checked against that same minimum.

Output: `dist/ShotQuill-0.0.0-<arch>.pkg`.

The product package contains a required application component for
`/Applications/ShotQuill.app` and a visible CLI component that is selected by
default. The CLI installs two guarded links under `/usr/local/bin` and can be
deselected for an app-only installation; the preinstall check refuses to replace
unrelated commands. Installer choices control what is installed in the current
transaction; deselecting CLI during an upgrade does not remove a CLI component
from an older install. To switch an existing installation to app-only, use the
built-in uninstaller, then reinstall with CLI deselected. To inspect a local
artifact without installing it:

```bash
installer -showChoicesXML -pkg dist/ShotQuill-0.0.0-arm64.pkg
expanded=$(mktemp -d)
pkgutil --expand-full dist/ShotQuill-0.0.0-arm64.pkg "$expanded"
find "$expanded" -print
rm -rf "$expanded"
```

The default app signature is ad hoc and the outer PKG is unsigned. Setting
`SHOTQUILL_INSTALLER_IDENTITY` signs the product archive with an available
Developer ID Installer identity, but production distribution also requires
proper application signing and notarization. Installer authorization is based
on the system installation domain, not just the CLI checkbox, so an app-only
install under `/Applications` may still request administrator approval.

The build packages `packaging/macos/uninstall_pkg` as a hidden, required
component at
`/Library/PrivilegedHelperTools/com.wardmos.shotquill.uninstall`. It is a
one-shot script, not a daemon; the protected root-owned location prevents the
file from being replaced while macOS displays its authorization dialog.
`squill uninstall` treats a registered Homebrew cask as the installation owner;
otherwise a direct-PKG uninstall elevates only this fixed helper, never the
Python/PyInstaller process. Every launcher clears the helper environment. The
helper accepts no deletion paths. The CLI replaces its App-backed process with
the protected system-shell coordinator and therefore returns the privileged
helper's final status. The GUI launches a user coordinator, exits, and is
reopened if authorization is cancelled or removal fails before app removal. A
post-removal partial failure instead presents recovery steps. Before
authorization, the coordinator binds device/inode identities together with the
helper digest, literal-link digest, and the app's executable, Info.plist, and
sealed-resource digests. The privileged phase rejects extended ACLs and any
changed installation generation. It finds running processes by the actual
executable vnode, requests that they terminate, stages the bundle on the same
filesystem, then checks the staged vnode again before deletion. Root handles
CLI links only under immutable root-owned directory parents; a mutable Homebrew
prefix is cleaned through an atomic user-privilege quarantine. Only the three
fixed ShotQuill receipts are forgotten after payload removal. User data and save
folders are outside the deletion allowlist. The generated cask validates the
protected helper with a system-shell wrapper, then invokes the same synchronous
user/root coordinator instead of deleting launchd or CLI paths broadly. It
selects the same guarded PKG CLI component rather than creating Homebrew
`binary` artifacts, so both installation channels share one pair of CLI links
and the same target-aware cleanup.

Both the PR package workflow and the tag release workflow run the shared
`smoke_pkg.sh` install/uninstall probe before publishing artifacts. They also
render the exact generated Cask through `smoke_cask.sh`, load it for every
macOS/architecture combination with Homebrew, and run Homebrew's Cask style
checks. The generated Cask declares macOS 13 (Ventura) as its minimum.

## Linux AppImage

Run on Linux. The AppImage is CLI/MCP-only and does not package the menu-bar GUI.
The release workflow builds it on Ubuntu 22.04 to keep the glibc floor at 2.35.

```bash
python -m pip install -U pip
python -m pip install -c packaging/constraints-release.txt -e . pyinstaller
bash packaging/linux/build_appimage.sh 0.0.0
```

For a local launch smoke test:

```bash
export APPIMAGE_EXTRACT_AND_RUN=1
dist/ShotQuill-0.0.0-x86_64.AppImage --version
QT_QPA_PLATFORM=offscreen dist/ShotQuill-0.0.0-x86_64.AppImage doctor
```

Minimal systems may need the same Qt runtime libraries installed by CI, such as
`libegl1`, `libgl1`, `libdbus-1-3`, and `libxkbcommon0`.

Output: `dist/ShotQuill-0.0.0-x86_64.AppImage`.

## Windows Bundle

Run on Windows in PowerShell. The script builds a windowed GUI executable and a
console CLI executable that share one payload.

```powershell
python -m pip install -U pip
python -m pip install -c packaging/constraints-release.txt -e . pyinstaller pillow
.\packaging\windows\build_exe.ps1 0.0.0
```

`pillow` is used to generate the `.ico` file. `UPX` is optional; PyInstaller uses
it automatically when it is available on `PATH`.

For a local launch smoke test:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\dist\shotquill\squill.exe --version
.\dist\shotquill\squill.exe doctor
```

Output: `dist\shotquill\ShotQuill.exe`, `dist\shotquill\squill.exe`, and the
shared `dist\shotquill\_internal\` payload.
