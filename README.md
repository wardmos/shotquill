# shotquill

A fast, privacy-respecting screenshot & annotation tool. **macOS first**, with a
cross-platform architecture (Windows / Linux planned).

> Status: early development (Phase 0 — project skeleton).

## Features (planned)

- Global hotkeys for **region** (`⌥A`) and **full-screen** (`⌥S`) capture — customizable
- On-screen annotation: rectangles, circles, arrows, lines, freehand, text,
  highlighter, and mosaic/blur for redaction
- Copy to clipboard or save to disk
- Menu-bar resident, no Dock clutter

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
