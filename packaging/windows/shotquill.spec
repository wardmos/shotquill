# -*- mode: python ; coding: utf-8 -*-
# Build the Windows GUI (windowed) and CLI (console) executables from a SINGLE
# Analysis so they share one _internal payload (Python + Qt) instead of shipping
# two full copies. Output: dist/shotquill/{ShotQuill.exe, squill.exe, _internal/}.
#
# Driven by packaging/windows/build_exe.ps1, which sets CWD to the repo root and
# renders packaging/windows/icon.ico before invoking PyInstaller on this spec.
# Both exes run the same dual-mode entry (packaging/entry.py): a bare
# ShotQuill.exe opens the tray GUI, squill.exe is the console build so the
# CLI/MCP surface can write to stdout and be driven by scripts and agents.

import os
import sys

# PyInstaller resolves relative paths in a spec against the spec's own directory,
# not the invoking CWD, so anchor everything on the repo root (two levels up from
# packaging/windows/). SPECPATH is injected by PyInstaller.
_root = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
_entry = os.path.join(_root, "packaging", "entry.py")
_src = os.path.join(_root, "src")

_ico = os.path.join(_root, "packaging", "windows", "icon.ico")
_icon = _ico if os.path.exists(_ico) else None

# ShotQuill only uses QtCore / QtGui / QtWidgets. PyInstaller's PySide6 hook
# would otherwise pull in the whole Qt stack (WebEngine, QML, 3D, Charts,
# Multimedia, ...) — hundreds of MB the app never touches. Drop the unused Qt
# modules, the macOS-only backends/pyobjc, and the Unix-only DBus/Network bits.
# Mirrors the macOS DMG excludes.
excludes = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtNetworkAuth", "PySide6.QtHttpServer",
    "PySide6.QtQml", "PySide6.QtQmlModels", "PySide6.QtQuick", "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets", "PySide6.QtQuickControls2",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtSpatialAudio",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSql", "PySide6.QtSvg", "PySide6.QtSvgWidgets",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtSerialBus",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtStateMachine",
    "PySide6.QtTextToSpeech", "PySide6.QtHelp", "PySide6.QtDesigner", "PySide6.QtUiTools",
    "PySide6.QtTest", "PySide6.QtXml", "PySide6.QtDBus", "PySide6.QtNetwork",
    "shotquill.app", "shotquill.capture.macos", "shotquill.capture.wayland",
    "shotquill.hotkeys.macos", "shotquill.autostart.macos", "shotquill.ocr.macos",
    "tkinter",
]

# The platform backends are imported lazily by string inside the factories, so
# PyInstaller's static analysis can't see them — name them explicitly or the
# frozen app would fail at runtime with ModuleNotFoundError.
hiddenimports = [
    "shotquill.capture.windows", "shotquill.capture.qtgrab",
    "shotquill.hotkeys.windows", "shotquill.autostart.windows", "shotquill.ocr.windows",
]

# UPX shrinks the Qt DLLs noticeably, but never compress the C runtime or Python
# core DLLs: UPX is known to corrupt these on Windows (and they barely compress).
# The versioned Python DLL is derived from the building interpreter so a Python
# bump (3.12 -> 3.13, ...) keeps it excluded without editing this list.
upx_exclude = [
    "vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll",
    "python3.dll", f"python{sys.version_info.major}{sys.version_info.minor}.dll",
]

a = Analysis(
    [_entry],
    pathex=[_src],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=2,
)
pyz = PYZ(a.pure)

# Two bootloaders over the same scripts: windowed (no console) for the tray GUI,
# console for the CLI. exclude_binaries=True keeps the shared payload in COLLECT.
exe_gui = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="ShotQuill",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=upx_exclude,
    console=False,
    icon=_icon,
)
exe_cli = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="squill",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=upx_exclude,
    console=True,
    icon=_icon,
)
coll = COLLECT(
    exe_gui, exe_cli,
    a.binaries, a.datas,
    strip=False,
    upx=True,
    upx_exclude=upx_exclude,
    name="shotquill",
)
