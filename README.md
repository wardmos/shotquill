# shotquill

[![CI](https://github.com/wardmos/shotquill/actions/workflows/ci.yml/badge.svg)](https://github.com/wardmos/shotquill/actions/workflows/ci.yml)

A fast, privacy-respecting screenshot & annotation tool. **macOS first**, with a
cross-platform architecture (Windows / Linux planned).

> Status: early development.

## Features

- Global hotkeys for **region** (`⌥A`) and **full-screen** (`⌥S`) capture — customizable
- On-screen annotation: rectangles, circles, arrows, lines, freehand, text,
  highlighter, and mosaic for redaction
- Copy to clipboard or save to disk
- Menu-bar resident, no Dock clutter

## Install

**Homebrew (recommended):**

```bash
brew install --cask wardmos/tap/shotquill
```

**Direct download:** grab the `.dmg` from
[Releases](https://github.com/wardmos/shotquill/releases), open it, and drag
Shotquill to Applications.

> Shotquill is open source and **ad-hoc signed (not notarized)** to keep the
> developer anonymous. On first launch macOS Gatekeeper will warn it can't
> verify the developer — **right-click the app → Open** once, or run:
>
> ```bash
> xattr -dr com.apple.quarantine /Applications/Shotquill.app
> ```
>
> The Homebrew cask strips quarantine automatically.

## Tech stack

Python 3.12 + [PySide6](https://doc.qt.io/qtforpython/) (Qt). Screen capture via
`mss`, global hotkeys via `pynput`, image ops via `Pillow`.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m shotquill        # launches the menu-bar app (macOS)
ruff check src tests       # lint
pytest                     # tests
```

> Capture, global hotkeys, and the selection overlay rely on macOS system
> frameworks, so they must be **run and tested on a Mac**. Pure logic and Qt
> widgets can be developed and tested headlessly on Linux
> (`QT_QPA_PLATFORM=offscreen`).

### macOS permissions

On first run, grant **Screen Recording** and **Input Monitoring** in
System Settings → Privacy & Security.

## License

[Apache-2.0](LICENSE). Copyright (C) 2026 wardmos.

This program bundles Qt via PySide6, which is licensed under the LGPLv3; the
corresponding license notices are included with distributed builds.
