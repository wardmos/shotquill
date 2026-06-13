# Build ShotQuill for Windows: a windowed GUI exe and a console CLI exe.
#
# Usage (from the repo root, on Windows, in PowerShell):
#   pip install pyinstaller pillow
#   pip install .                       # or: pip install .[windows-ocr]
#   .\packaging\windows\build_exe.ps1 <version>
#
# Produces, under dist\:
#   ShotQuill\ShotQuill.exe   - the menu-bar GUI (no console window). A bare
#                               launch opens the tray app; with arguments it
#                               runs the CLI, same dual-mode entry as macOS.
#   squill\squill.exe         - a console build of the same entry, so the CLI /
#                               MCP surface can write to stdout and be driven by
#                               scripts and agents (a --windowed exe has no
#                               console to print to).
#
# NOTE: this script has not been validated on a real Windows runner yet — it
# mirrors the macOS DMG build's PyInstaller invocation and Qt-module pruning.
# The CI Windows job (.github/workflows) is where it gets exercised; treat a
# green run there as the source of truth.
#
# On-device OCR is NOT bundled by default (the WinRT projection is a large,
# optional dependency). Install `shotquill[windows-ocr]` into the build
# environment before running this to fold it in.

$ErrorActionPreference = 'Stop'

$Version = if ($args.Count -ge 1) { $args[0] } else { '0.0.0' }
$Version = $Version -replace '^v', ''

$Root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
Set-Location $Root

if (Test-Path build) { Remove-Item -Recurse -Force build }
if (Test-Path dist)  { Remove-Item -Recurse -Force dist }

# ShotQuill only uses QtCore / QtGui / QtWidgets. PyInstaller's PySide6 hook
# would otherwise pull in the whole Qt stack (WebEngine, QML, 3D, Charts,
# Multimedia, …) — hundreds of MB the app never touches. Drop the unused Qt
# modules, the macOS-only backends/pyobjc, and the Unix-only DBus/Network bits.
# Mirrors the macOS DMG excludes.
$QtExcludes = @(
  'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick',
  'PySide6.QtWebChannel', 'PySide6.QtWebSockets', 'PySide6.QtNetworkAuth', 'PySide6.QtHttpServer',
  'PySide6.QtQml', 'PySide6.QtQmlModels', 'PySide6.QtQuick', 'PySide6.QtQuick3D',
  'PySide6.QtQuickWidgets', 'PySide6.QtQuickControls2',
  'PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.Qt3DInput', 'PySide6.Qt3DLogic',
  'PySide6.Qt3DAnimation', 'PySide6.Qt3DExtras',
  'PySide6.QtCharts', 'PySide6.QtDataVisualization', 'PySide6.QtGraphs',
  'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets', 'PySide6.QtSpatialAudio',
  'PySide6.QtPdf', 'PySide6.QtPdfWidgets', 'PySide6.QtSql', 'PySide6.QtSvg', 'PySide6.QtSvgWidgets',
  'PySide6.QtBluetooth', 'PySide6.QtNfc', 'PySide6.QtPositioning', 'PySide6.QtLocation',
  'PySide6.QtSensors', 'PySide6.QtSerialPort', 'PySide6.QtSerialBus',
  'PySide6.QtRemoteObjects', 'PySide6.QtScxml', 'PySide6.QtStateMachine',
  'PySide6.QtTextToSpeech', 'PySide6.QtHelp', 'PySide6.QtDesigner', 'PySide6.QtUiTools',
  'PySide6.QtTest', 'PySide6.QtXml', 'PySide6.QtDBus', 'PySide6.QtNetwork',
  'shotquill.app', 'shotquill.capture.macos', 'shotquill.capture.wayland',
  'shotquill.hotkeys.macos', 'shotquill.autostart.macos', 'shotquill.ocr.macos',
  'tkinter'
)

# The platform backends are imported lazily by string inside the factories, so
# PyInstaller's static analysis can't see them — name them explicitly or the
# frozen app would fail at runtime with ModuleNotFoundError.
$HiddenImports = @(
  'shotquill.capture.windows', 'shotquill.capture.qtgrab',
  'shotquill.hotkeys.windows', 'shotquill.autostart.windows', 'shotquill.ocr.windows'
)

# Best-effort icon: convert the committed master PNG to .ico (needs Pillow).
# Build without --icon if conversion isn't available rather than failing.
$IconArgs = @()
$Ico = Join-Path $PSScriptRoot 'icon.ico'
try {
  python (Join-Path $PSScriptRoot 'make_icon.py') $Ico
  if (Test-Path $Ico) { $IconArgs = @('--icon', $Ico) }
} catch {
  Write-Warning "icon.ico not generated ($_); building without a custom icon"
}

$Common = @(
  '--noconfirm', '--clean', '--optimize', '2', '--paths', 'src'
) + ($QtExcludes | ForEach-Object { '--exclude-module', $_ }) `
  + ($HiddenImports | ForEach-Object { '--hidden-import', $_ }) `
  + $IconArgs

Write-Host "Building ShotQuill.exe (GUI) $Version ..."
pyinstaller @Common --windowed --name ShotQuill packaging\entry.py

Write-Host "Building squill.exe (CLI) $Version ..."
pyinstaller @Common --console --name squill packaging\entry.py

# Trim payload --exclude-module can't reach (it works per-module, not per-file):
# the English-only build needs no Qt translations, and ShotQuill writes only PNG
# (built into QtGui) and JPEG (qjpeg plugin), so the other imageformat plugins
# are dead weight.
foreach ($app in @('dist\ShotQuill', 'dist\squill')) {
  $qt = Join-Path $app '_internal\PySide6\Qt'
  if (-not (Test-Path $qt)) { $qt = Join-Path $app 'PySide6\Qt' }  # layout varies by PyInstaller
  $tr = Join-Path $qt 'translations'
  if (Test-Path $tr) { Remove-Item -Recurse -Force $tr }
  $imgfmt = Join-Path $qt 'plugins\imageformats'
  if (Test-Path $imgfmt) {
    Get-ChildItem $imgfmt -File | Where-Object { $_.Name -notlike 'qjpeg*' } | Remove-Item -Force
  }
}

Write-Host "Done. GUI: dist\ShotQuill\ShotQuill.exe   CLI: dist\squill\squill.exe"
