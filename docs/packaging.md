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

## macOS DMG

Run on macOS. The script uses macOS tools such as `sips`, `iconutil`,
`codesign`, and `hdiutil`.

```bash
python -m pip install -U pip
python -m pip install -c packaging/constraints-release.txt -e . pyinstaller
SHOTQUILL_DMG_FORMAT=UDZO bash packaging/macos/build_dmg.sh 0.0.0 arm64
```

`SHOTQUILL_DMG_FORMAT=UDZO` uses faster zlib compression for local smoke builds.
Release builds omit it and use the script default, `ULMO`, for smaller DMGs.

The final argument selects the architecture. Use `arm64` on Apple Silicon,
`x86_64` on Intel Macs, or `universal2` to validate the universal build path.
Building non-native or universal2 bundles requires a universal2 Python and
compatible universal2 wheels.

Output: `dist/ShotQuill-0.0.0-<arch>.dmg`.

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
