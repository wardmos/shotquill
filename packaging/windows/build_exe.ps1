# Build ShotQuill for Windows: a windowed GUI exe and a console CLI exe, sharing
# one payload.
#
# Usage (from the repo root, on Windows, in PowerShell):
#   pip install pyinstaller pillow
#   pip install .                       # or: pip install .[windows-ocr]
#   .\packaging\windows\build_exe.ps1 <version>
#
# Produces, under dist\shotquill\:
#   ShotQuill.exe   - the menu-bar GUI (no console window). A bare launch opens
#                     the tray app; with arguments it runs the CLI, same
#                     dual-mode entry as macOS.
#   squill.exe      - a console build of the same entry, so the CLI / MCP surface
#                     can write to stdout and be driven by scripts and agents (a
#                     --windowed exe has no console to print to).
#   _internal\      - the shared Python + Qt payload. Both exes load it, so the
#                     bundle ships ONE copy instead of two (see shotquill.spec).
#
# NOTE: this script has not been validated on a real Windows runner yet — it
# mirrors the macOS DMG build's PyInstaller invocation and Qt-module pruning.
# The CI Windows job (.github/workflows) is where it gets exercised; treat a
# green run there as the source of truth.
#
# On-device OCR is NOT bundled by default (the WinRT projection is a large,
# optional dependency). Install `shotquill[windows-ocr]` into the build
# environment before running this to fold it in.
#
# UPX (optional) shrinks the Qt DLLs noticeably if it is on PATH; PyInstaller
# uses it automatically and skips it (with a warning) when absent. The C
# runtime / Python core DLLs are excluded from UPX in shotquill.spec.

$ErrorActionPreference = 'Stop'

$Version = if ($args.Count -ge 1) { $args[0] } else { '0.0.0' }
$Version = $Version -replace '^v', ''

$Root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
Set-Location $Root

if (Test-Path build) { Remove-Item -Recurse -Force build }
if (Test-Path dist)  { Remove-Item -Recurse -Force dist }

# Best-effort icon: convert the committed master PNG to .ico (needs Pillow). The
# spec builds without an icon if conversion isn't available rather than failing.
$Ico = Join-Path $PSScriptRoot 'icon.ico'
try {
  python (Join-Path $PSScriptRoot 'make_icon.py') $Ico
} catch {
  Write-Warning "icon.ico not generated ($_); building without a custom icon"
}

# One Analysis, two bootloaders (GUI + CLI), one shared _internal — see the spec.
# The module excludes, hidden imports, --optimize 2 and UPX policy all live there
# so a local build and the CI build stay byte-for-byte aligned.
Write-Host "Building ShotQuill.exe (GUI) + squill.exe (CLI) $Version ..."
pyinstaller --noconfirm --clean packaging\windows\shotquill.spec

# Trim payload --exclude-module can't reach (it works per-module, not per-file).
# PyInstaller's PySide6 hook copies whole plugin subtrees and Qt translations
# regardless of which Qt modules survive; nothing on ShotQuill's path loads them.
# Mirrors the macOS DMG prune and the Linux prune_bundle.py keep policy.
$qt = Join-Path 'dist\shotquill' '_internal\PySide6\Qt'
if (-not (Test-Path $qt)) { $qt = Join-Path 'dist\shotquill' 'PySide6\Qt' }  # layout varies by PyInstaller

# Qt UI translations: the build is English-only.
$tr = Join-Path $qt 'translations'
if (Test-Path $tr) { Remove-Item -Recurse -Force $tr }

# Plugins are dlopen-ed by name, so --exclude-module never reaches them; prune by
# an explicit keep policy. A dropped plugin is never a dependency of a kept DLL,
# so dropping it can't break a kept path.
$plugins = Join-Path $qt 'plugins'
if (Test-Path $plugins) {
  # imageformats: ShotQuill writes only PNG (built into QtGui) and JPEG (qjpeg).
  $imgfmt = Join-Path $plugins 'imageformats'
  if (Test-Path $imgfmt) {
    Get-ChildItem $imgfmt -File | Where-Object { $_.Name -notlike 'qjpeg*' } | Remove-Item -Force
  }
  # platforms: keep only what can construct a QGuiApplication here — qwindows for
  # the real session, qoffscreen for headless/tests, qminimal as a fallback.
  # Drop qdirect2d / qminimalegl / qwebgl (opt-in / embedded targets).
  $platforms = Join-Path $plugins 'platforms'
  if (Test-Path $platforms) {
    $keep = @('qwindows.dll', 'qoffscreen.dll', 'qminimal.dll')
    Get-ChildItem $platforms -File | Where-Object { $keep -notcontains $_.Name } | Remove-Item -Force
  }
  # iconengines: QtSvg is excluded, so the SVG icon engine is dead weight.
  $svgicon = Join-Path $plugins 'iconengines\qsvgicon.dll'
  if (Test-Path $svgicon) { Remove-Item -Force $svgicon }
  # platforminputcontexts: the virtual keyboard pulls in QtQuick (also excluded).
  $inputctx = Join-Path $plugins 'platforminputcontexts'
  if (Test-Path $inputctx) {
    Get-ChildItem $inputctx -File | Where-Object { $_.Name -like '*virtualkeyboard*' } | Remove-Item -Force
  }
}

# Layout-drift guard: if a Qt/PyInstaller bump relocates these, the prunes above
# silently no-op and could drop something load-bearing. The bundle is useless
# without the Windows platform plugin and the JPEG codec — fail the build.
foreach ($must in @('qwindows.dll', 'qjpeg.dll')) {
  if (-not (Get-ChildItem 'dist\shotquill' -Recurse -File -Filter $must)) {
    Write-Error "prune removed a required plugin ($must); aborting"
    exit 1
  }
}

Write-Host "Done. GUI: dist\shotquill\ShotQuill.exe   CLI: dist\shotquill\squill.exe"
