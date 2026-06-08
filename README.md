<p align="center">
  <img src="packaging/macos/icon.png" alt="ShotQuill icon" width="128" height="128">
</p>

<h1 align="center">ShotQuill</h1>

<p align="center">
  A fast, privacy-respecting screenshot &amp; annotation tool for macOS.
</p>

<p align="center">
  <a href="https://github.com/wardmos/shotquill/actions/workflows/ci.yml"><img src="https://github.com/wardmos/shotquill/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/platform-macOS-blue" alt="Platform: macOS">
  <img src="https://img.shields.io/badge/python-3.12-blue" alt="Python 3.12">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-green" alt="License: Apache 2.0"></a>
</p>

ShotQuill lives in your menu bar and turns a screenshot into a finished, shareable
image in one motion: press a hotkey, then let the pointer pick a window / region /
the whole screen, and it's saved and on your clipboard — or drop into a built-in
editor to annotate, redact, and extract text first. **macOS first**, built on a
cross-platform architecture (Windows / Linux planned).

> **Status:** early development — usable day-to-day, but expect rough edges.

---

## Highlights

- **Two capture hotkeys**, both customizable:
  - **Capture** (`⌥A`) — one overlay, mode chosen by the pointer: click an
    app window to grab just that window (real pixels, even when partly
    covered), click empty space to grab the whole screen, or drag a rectangle
    for a region
    with a live size readout. The window under the pointer is spotlit against
    the dimmed desktop so the click target is always clear; hovering can also
    fully highlight it before the click — with the window's own pixels lifted
    out from under whatever overlaps it — after a configurable rest time
    (Settings, "Highlight window after"; off by default). A pixel loupe follows the pointer — magnified
    pixels, a crosshair, and the position/colour under the cursor — so region
    boundaries land exactly where you want.
  - **Full screen** (`⌥S`) — every display at once, instantly.
- **Hands-free by default** — a capture is saved to your folder **and** copied to
  the clipboard automatically, no extra keypress. Fully configurable (see below).
- **Annotation editor** — rectangles, ellipses, arrows, lines, freehand pen,
  highlighter, text, and **mosaic redaction** that pixelates the real pixels (not
  just an overlay, so the sensitive data never survives in the exported image).
- **On-device OCR** via Apple's Vision framework — pull text out of a shot,
  fully offline, no network, no API key. Recognizes Chinese (Simplified) + English.
- **Pin to screen** — float an annotated shot on top of the desktop for reference;
  drag to move, double-click or `Esc` to dismiss.
- **Bilingual UI** — English / 中文, switchable in Settings (defaults to English).
- **Menu-bar resident** — no Dock clutter; optional launch-at-login.

---

## Install

**Homebrew (recommended):**

```bash
brew install --cask wardmos/tap/shotquill
```

`brew upgrade` keeps it current.

**Direct download:** grab the `.dmg` from
[Releases](https://github.com/wardmos/shotquill/releases) — `arm64` for Apple
Silicon, `x86_64` for Intel Macs, or `universal2` if unsure (works on both,
roughly twice the size) — open it, and drag ShotQuill to your Applications
folder. Each release ships a `.sha256` sidecar so you can verify the download:

```bash
shasum -a 256 -c ShotQuill-*.dmg.sha256
```

> ShotQuill is open source and **ad-hoc signed (not notarized)** so the developer
> can stay anonymous. On first launch macOS Gatekeeper will warn that it can't
> verify the developer — **right-click the app → Open** once, or run:
>
> ```bash
> xattr -dr com.apple.quarantine /Applications/ShotQuill.app
> ```
>
> The Homebrew cask strips quarantine automatically, so this only applies to the
> direct download.

---

## Usage

ShotQuill runs in the menu bar. Click its icon for the menu, or use the global
hotkeys from anywhere.

### Capture hotkeys

| Action         | Default | Notes                                                                                |
| -------------- | ------- | ------------------------------------------------------------------------------------ |
| Capture        | `⌥A`    | Click a window to grab it, click empty space for full screen, or drag for a region. `Esc` / right-click cancels. |
| Full-screen    | `⌥S`    | All displays composited into one image, instantly.                                   |

Both are remappable in **Settings** (any combination of `⌘ ⌃ ⌥ ⇧` + a key).

### What happens after a capture

By default ShotQuill is **hands-free**: the shot is saved to your folder and
copied to the clipboard immediately, with a brief screen flash to confirm — no
editor, no keypress. You can change this in Settings → *After capture*:

| Auto-save | Auto-copy | Result                                                       |
| :-------: | :-------: | ------------------------------------------------------------ |
|     ✅     |     ✅     | Saved **and** copied, no editor (default).                   |
|     ✅     |     —     | Saved only.                                                  |
|     —     |     ✅     | Copied only.                                                 |
|     —     |     —     | Opens the **annotation editor** instead (see below).         |

### Annotation editor

When both auto-output toggles are off (or whenever you want to mark a shot up),
the editor opens with a toolbar:

- **Tools:** select, rectangle, ellipse, arrow, line, pen, highlighter, mosaic,
  text — with adjustable color and stroke width, plus undo / redo.
- **Copy Text** runs OCR on the capture and copies the recognized text.
- **Pin** floats the annotated shot on top of the desktop.

Keyboard:

| Key            | Action                                  |
| -------------- | --------------------------------------- |
| `Space`        | Copy to the clipboard, then close       |
| `Enter`        | Save to your folder, then close         |
| `⌘Z` / `⌘⇧Z`   | Undo / redo                             |
| `Esc`          | Close without saving                    |

The copy and save keys are configurable in Settings, and each can be
disabled individually. Settings rejects keys that would clash with the
built-in editor shortcuts (copy/save/undo/redo/`Esc`), with each other,
or with a global capture hotkey.

### Saved files

Captures are written to `~/Pictures/ShotQuill` by default (configurable), named
with a timestamp — e.g. `ShotQuill 2026-06-04 14.30.00.png`. Choose **PNG** or
**JPG** in Settings.

---

## Command line (scripts & agents)

ShotQuill ships a CLI — `shotquill`, or the short alias `squill` — so shell
scripts and AI agents can capture without the GUI. Run bare it launches the
menu-bar app; with a subcommand it stays headless:

```bash
squill capture                            # full screen → temp file, path on stdout
squill capture --app safari -o shot.png   # front-most matching window
squill capture --region 0,0,800,600 -o -  # stream PNG bytes to a pipe
squill windows --json                     # list windows, front-most first
squill capture -o - | squill ocr -        # capture → on-device OCR, one pipe
squill doctor                             # capability & permission report
```

The parts agents rely on:

- **One path on stdout.** `capture` writes one file and prints exactly one
  absolute path; warnings go to stderr. It never touches the clipboard, and
  defaults to a private temp dir — pass `-o` to keep a shot.
- **Exit codes are the contract**: `0` ok · `2` usage · `3` permission denied ·
  `4` capability unavailable on this platform/session · `5` no window matched.
- **Permissions follow the invoking app.** macOS attributes Screen Recording to
  whatever launched the CLI (your terminal, an agent host) — the consent dialog
  names the real controller, and `squill doctor` reports what is missing.
- **Every programmatic capture is audit-logged** — metadata only, never
  pixels — to a JSONL file (`~/Library/Logs/shotquill/audit.log` on macOS,
  `$XDG_STATE_HOME/shotquill/audit.log` elsewhere) and mirrored into the OS log
  store (unified log / journald), which user-space processes cannot rewrite.
  Each entry records the process chain that drove the capture.

### MCP server

`squill mcp` serves the [Model Context Protocol](https://modelcontextprotocol.io)
over stdio, so MCP clients (Claude Code, Claude Desktop, …) can give their
agents eyes on your screen. Register it:

```bash
claude mcp add shotquill -- squill mcp
```

or in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "shotquill": { "command": "squill", "args": ["mcp"] }
  }
}
```

Four tools: **capture** (full screen / window by id or app+title / region;
returns the image inline — pass `max_width` to downscale and save context;
`save_path` optionally persists), **list_windows**, **ocr** (a file, or
capture-and-recognize fully in memory so reading on-screen text costs no
image tokens), and **doctor**.

Know what you are opting into:

- **Captured pixels go to the agent's model.** That is the point of the
  feature — the image is returned to the MCP client, which sends it to
  whatever model the agent uses. ShotQuill itself still makes no network
  requests; if that trade-off isn't right for the moment, don't start the
  server.
- **The session is bounded.** stdio only — no socket, no port; only the MCP
  client that spawned the server can talk to it, and it dies when the client
  exits (or after `--timeout SECONDS`, if you pass one). Nothing runs unless
  you registered it.
- **Same accountability as the CLI**: macOS attributes Screen Recording to
  the MCP client app, and every screen-touching tool call lands in the audit
  log with `via: "mcp"`. Your MCP client's per-tool-call approval settings
  add a confirmation layer on top if you want one.

---

## Configuration

Open **Settings…** from the menu-bar icon:

- **Language** — English / 中文.
- **Save folder** & **image format** (PNG / JPG).
- **Hotkeys** for both capture modes.
- **Editor finish keys** — the in-editor copy and save keys (Space / Enter by
  default), each with its own enable toggle.
- **After capture** — auto-save and/or auto-copy toggles (above).
- **Include mouse pointer** (off) — composite the cursor into captures.
- **Launch at login** — installs a per-user `LaunchAgent`.
- **Flash on capture** (on) and **Sound on capture** (off) — capture feedback.

---

## Privacy

ShotQuill is built to be trustworthy, and it's open source so you can verify it:

- **No keylogging.** The global-hotkey listener only checks for your configured
  shortcut combos; it never records, stores, or forwards keystrokes.
- **OCR is on-device.** Text recognition uses Apple's Vision framework locally —
  nothing is uploaded, and it works with no network connection.
- **Redaction is real.** The mosaic tool rewrites the underlying pixels before
  export, so blurred-out content isn't recoverable from the saved image.
- **No telemetry.** ShotQuill makes no network requests of its own.
- **Programmatic captures are accountable.** Scripts and AI agents using the
  CLI or the MCP server go through the same OS consent as any app — macOS
  attributes Screen Recording to the invoking app, so the permission dialog
  names the real controller — and every programmatic capture leaves an audit
  entry (metadata only, never pixels) in a local JSONL file plus the
  tamper-resistant OS log store. The MCP server is strictly opt-in and, by
  design, returns captures to the agent's model — see the MCP section for
  what that means.

---

## Tech stack

Python 3.12 + [PySide6](https://doc.qt.io/qtforpython/) (Qt) for a self-drawn,
cross-platform UI:

| Concern               | Library                                              |
| --------------------- | ---------------------------------------------------- |
| GUI / editor canvas   | PySide6 (Qt Widgets + Graphics View)                 |
| Screen capture        | ScreenCaptureKit (macOS 14+), `CGWindowList*` fallback |
| Global hotkeys        | `pynput`                                             |
| Image processing      | Qt (`QImage`)                                        |
| OCR                   | `pyobjc` → Apple Vision                               |

Platform-specific code (capture, hotkeys, OCR, autostart) sits behind small
`base.py` interfaces, so the editor and output layers stay portable and adding a
new OS means implementing those interfaces rather than touching the UI.

---

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

python -m shotquill              # launch the menu-bar app (macOS)
ruff check src tests             # lint
ruff format --check src tests    # formatting
pytest                           # tests
```

> Screen capture, global hotkeys, and the full-screen overlays rely on macOS
> system frameworks, so they must be **run and tested on a Mac**. Pure logic and
> Qt widgets can be developed and tested headlessly on Linux with
> `QT_QPA_PLATFORM=offscreen` (this is what CI does). Window-activation
> scenarios (`tests/test_activation_macos.py`) only run under a real macOS
> window server — the macOS CI leg, or a Mac without `QT_QPA_PLATFORM` set —
> because the offscreen platform performs no activation arbitration at all.

### macOS permissions

On first run, grant these in **System Settings → Privacy & Security**:

- **Screen Recording** — required to capture the screen and enumerate windows.
- **Input Monitoring** — required for the global capture hotkeys to work while
  other apps are focused.

ShotQuill's Settings dialog shows the live status of both permissions, with a
button that jumps straight to the right privacy pane.

---

## Roadmap

- [x] Smart (window / region / full-screen) + full-screen capture
- [x] Annotation editor (shapes, text, highlighter, mosaic) + pin-to-screen
- [x] On-device OCR
- [x] Hands-free auto save + clipboard
- [x] CLI for scripts & AI agents (`squill capture` / `windows` / `ocr` / `doctor`)
- [x] MCP server, so agents can capture and read the screen over Model Context Protocol
- [ ] Windows & Linux backends (X11 full-screen/region capture already works
      via the CLI; Wayland, window capture, and the GUI do not yet)
- [ ] Scrolling / long-page capture

---

## Contributing

Issues and pull requests are welcome. Please run `ruff check`, `ruff format`, and
`pytest` before submitting; CI runs the same on Linux + macOS.

---

## License

[Apache-2.0](LICENSE). Copyright (C) 2026 wardmos.

ShotQuill bundles Qt via PySide6, which is licensed under the LGPLv3; the
corresponding license notices are included with distributed builds.
