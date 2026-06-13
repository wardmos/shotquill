<p align="center">
  <img src="packaging/macos/icon.png" alt="ShotQuill icon" width="128" height="128">
</p>

<h1 align="center">ShotQuill</h1>

<p align="center">
  A fast, privacy-respecting screenshot &amp; annotation tool for macOS &mdash; with Linux/X11 and Windows GUI plus cross-platform CLI/MCP support.
</p>

<p align="center">
  <a href="https://github.com/wardmos/shotquill/actions/workflows/ci.yml"><img src="https://github.com/wardmos/shotquill/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-blue" alt="Platform: macOS | Linux | Windows">
  <img src="https://img.shields.io/badge/python-3.10+-blue" alt="Python 3.10+">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-green" alt="License: Apache 2.0"></a>
</p>

ShotQuill lives in your menu bar and turns a screenshot into a finished, shareable
image in one motion: press a hotkey, then let the pointer pick a window / region /
the whole screen, and it's saved and on your clipboard — or drop into a built-in
editor to annotate, redact, and extract text first.

- **macOS** — full GUI, CLI, MCP, and on-device OCR (Apple Vision).
- **Linux / X11** — full menu-bar GUI plus CLI / MCP, including window
  enumeration (smart-capture window highlight, `squill windows`, and blocklist
  redaction of full-screen grabs) and on-device OCR via Tesseract when it's
  installed.
- **Linux / Wayland** — CLI / MCP via `xdg-desktop-portal`. Global hotkeys are
  blocked by Wayland by design (use the tray menu, or bind a compositor-level
  shortcut to `squill capture`); the GUI surfaces this loudly instead of failing
  silently.
- **Windows** — full menu-bar GUI plus CLI / MCP: capture, window enumeration
  (Win32), global hotkeys, and launch-at-login (the per-user `Run` key). On-device
  OCR isn't implemented yet, so `squill ocr` is unavailable.

> **Status:** early development — macOS is usable day-to-day; the Linux GUI is
> newly landed and still being smoothed out. Expect rough edges either way.

**Jump to:**
[Highlights](#highlights) ·
[Install](#install) ·
[Usage](#usage) ·
[CLI](#command-line-scripts--agents) ·
[MCP server](#mcp-server) ·
[App blocklist](#app-blocklist) ·
[Configuration](#configuration) ·
[Troubleshooting](#troubleshooting) ·
[Privacy](#privacy) ·
[Development](#development) ·
[Uninstall](#uninstall) ·
[Roadmap](#roadmap)

---

## Highlights

- **Two capture hotkeys**, both customizable:
  - **Capture** (`⌥A`) — one overlay; the pointer picks the mode:
    - **click a window** — grab just it, real pixels even when partly covered;
    - **click empty space** — the whole screen;
    - **drag** — a region, with a live size readout and a pixel loupe (magnified
      pixels + crosshair + position/colour) for precise edges.

    The hovered target is spotlit against the dimmed desktop. An optional delay
    (Settings → *Highlight window after*, off by default) fully highlights a
    window first, lifting its pixels out from under any overlap.
  - **Full screen** (`⌥S`) — every display at once, instantly.
- **Hands-free by default** — a capture is saved to your folder **and** copied to
  the clipboard automatically, no extra keypress. Fully configurable (see below).
- **Annotation editor** — rectangles, ellipses, arrows, lines, freehand pen,
  highlighter, text, and **mosaic redaction** that pixelates the real pixels (not
  just an overlay, so the sensitive data never survives in the exported image).
- **On-device OCR** via Apple's Vision framework — pull text out of a shot,
  fully offline, no network, no API key. Recognizes Chinese (Simplified) + English.
  *(macOS only for now; a tesseract backend for Linux is on the roadmap.)*
- **Scriptable & agent-ready** — a headless [CLI](#command-line-scripts--agents)
  (`squill capture` / `windows` / `ocr` / `doctor` — one path on stdout, exit
  codes as the contract) and a built-in [MCP server](#mcp-server) that gives AI
  agents eyes on your screen. Every programmatic capture is audit-logged.
- **Pin to screen** — float an annotated shot on top of the desktop for reference;
  drag to move, double-click or `Esc` to dismiss.
- **Bilingual UI** — English / 中文, switchable in Settings (defaults to English).
- **Menu-bar resident** — no Dock clutter; optional launch-at-login.

---

## Install

### macOS

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

### Linux

Two channels, pick by what you need:

| You want… | Use |
| --- | --- |
| The **menu-bar GUI** + CLI + MCP | **pipx** (or pip) install from PyPI |
| Just the **CLI / MCP** in one self-contained binary | **AppImage** from Releases |

**pipx (recommended for the GUI):**

```bash
pipx install shotquill                # menu-bar app, plus `shotquill` and `squill`
squill install-desktop-entry          # add ShotQuill to your app menu (pipx-only step)
shotquill                             # launch the menu-bar app
```

`pipx upgrade shotquill` keeps it current. `pip install --user shotquill` works
too if you prefer pip — in that case the `.desktop` launcher and icon land
under `~/.local/share` automatically, so you can skip the `install-desktop-entry`
step. (`pipx` stores data files inside its private venv, which the desktop
doesn't search, hence the one-liner.)

**AppImage (CLI / MCP only):** download the `.AppImage` from
[Releases](https://github.com/wardmos/shotquill/releases), `chmod +x`, run.
It bundles Python + Qt headless bits (no QtWidgets, no GUI) so the binary
stays small and the CLI/MCP work even where the GUI's dependencies wouldn't.
Built on Ubuntu 22.04 → glibc 2.35 floor (Ubuntu 22.04+ / Debian 12+).

**Wayland users** also need `xdg-desktop-portal` plus a portal backend for
your desktop (`xdg-desktop-portal-gnome`, `-kde`, or `-wlr`) — `squill doctor`
will tell you when it's missing. **X11 users** need nothing extra.

> **Linux GUI notes.** ShotQuill needs a system tray to run. GNOME 42+ shipped
> without legacy tray support — install the **AppIndicator and KStatusNotifierItem
> Support** extension; KDE, XFCE, MATE, and Cinnamon already include a tray.
> Global hotkeys (`Alt+A`, `Alt+S`) work on X11; on Wayland the OS blocks them
> by design and ShotQuill surfaces the reason via a notification so you can
> fall back to the tray menu or a compositor-level shortcut.

---

## Usage

ShotQuill runs in the menu bar. Click its icon for the menu, or use the global
hotkeys from anywhere.

### Capture hotkeys

| Action         | macOS | Linux / Windows | Notes                                                                                |
| -------------- | ----- | --------------- | ------------------------------------------------------------------------------------ |
| Capture        | `⌥A`  | `Alt+A` | Click a window to grab it, click empty space for full screen, or drag for a region. `Esc` / right-click cancels. |
| Full-screen    | `⌥S`  | `Alt+S` | All displays composited into one image, instantly.                                   |

Both are remappable in **Settings** — any combination of modifiers (`⌘ ⌃ ⌥ ⇧`
on macOS, `Super+ Ctrl+ Alt+ Shift+` on Linux/Windows) plus a key. Hotkey labels
in the tray menu and Settings render natively per platform (Apple keycap glyphs
on macOS, text labels on Linux/Windows).

> **Linux / Wayland**: global hotkeys are blocked by the compositor; ShotQuill
> raises a notification at startup so you can fall back to the tray menu, or
> bind a compositor-level shortcut to `squill capture` (full screen) /
> `squill capture --interactive` (planned).

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
scripts and AI agents can capture without the GUI. Run it bare and it launches
the menu-bar app; with a subcommand it stays headless:

```bash
squill capture                            # full screen → temp file, path on stdout
squill capture --app safari -o shot.png   # front-most matching window
squill capture --region 0,0,800,600 -o -  # stream PNG bytes to a pipe
squill capture --display 1 -o second.png  # one monitor (`squill displays` lists them)
squill capture --json --max-width 1024    # downscaled, JSON metadata on stdout
squill capture --deterministic -o shot.png # byte-stable output for golden tests
squill windows --json                     # list windows, front-most first
squill displays                           # list monitors and their indexes
squill ocr --app safari                   # screen → on-device OCR, one step
squill doctor                             # capability & permission report
```

The parts agents rely on:

- **One path on stdout.** `capture` writes one file and prints exactly one
  absolute path; warnings go to stderr. It never touches the clipboard, and
  defaults to a private temp dir — pass `-o` to keep a shot. `--json` swaps
  the bare path for one JSON object (path, target, size, ambiguity count),
  and `--max-width` downscales before the image reaches a vision model.
- **Byte-stable captures for tests.** `--deterministic` pins the embedded DPI
  and strips PNG timestamp/text chunks (and forces the cursor off), so identical
  pixels always encode to identical bytes — what a golden-image diff or content
  hash needs across machines. The MCP `capture` tool takes the same flag.
- **OCR reads the screen directly.** `squill ocr --app safari` (or
  `--window-id`, `--region`, or nothing for the full screen) captures and
  recognizes in memory — no file, no pipe. `squill ocr shot.png` and
  `squill ocr -` still read a file or stdin.
- **Exit codes are the contract**: `0` ok · `2` usage · `3` permission denied ·
  `4` capability unavailable on this platform/session · `5` no window or
  display matched · `6` blocked by the app blocklist. Every `--help` prints
  them, so the contract is discoverable without this README;
  `python -m shotquill` accepts the same subcommands.
- **Permissions follow the invoking app.** macOS attributes Screen Recording to
  whatever launched the CLI (your terminal, an agent host) — the consent dialog
  names the real controller, and `squill doctor` reports what is missing.
- **Every programmatic capture is audit-logged** — metadata only, never
  pixels — to a JSONL file (`~/Library/Logs/shotquill/audit.log` on macOS,
  `%LOCALAPPDATA%\shotquill\Logs\audit.log` on Windows, `$XDG_STATE_HOME/shotquill/audit.log`
  elsewhere) and mirrored into the OS log store (unified log / journald) where one
  exists, which user-space processes cannot rewrite.
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

Five tools: **capture** (full screen / window by id or app+title / one
monitor by `display` index / region; returns the image inline — pass
`max_width` to downscale and save context; `save_path` optionally persists),
**list_windows**, **list_displays**, **ocr** (a file, or
capture-and-recognize fully in memory so reading on-screen text costs no
image tokens), and **doctor**. Built for agent ergonomics: every tool
declares an `outputSchema` and returns typed `structuredContent` (no
re-parsing JSON out of text), the read-only tools are annotated
`readOnlyHint` so hosts can auto-approve them, and every in-band error
carries a `type` plus a `hint` naming the recovery step (`no_match` →
"call list_windows", `permission` → "call doctor", …).

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

### App blocklist

Name apps that must never be captured — a password manager, your keychain —
and ShotQuill refuses to capture their windows and **redacts them out of
full-screen and region captures** (an opaque block painted over the pixels,
not an overlay, so nothing sensitive survives in the image). This covers the
GUI, the CLI, and the MCP server alike.

Manage it from **Settings → Blocked apps…** (on macOS, pick from the running
apps), from the command line, or by hand-editing the JSON file directly:

```bash
squill blocklist add --bundle-id com.1password.1password
squill blocklist add --name keychain      # app-name substring
squill blocklist list                     # --json for machines
squill blocklist remove --name keychain
```

The list is a plain JSON file, read by every surface so one rule protects them
all:

- macOS: `~/Library/Application Support/shotquill/blocklist.json`
- Windows: `%APPDATA%\shotquill\blocklist.json`
- elsewhere: `$XDG_CONFIG_HOME/shotquill/blocklist.json`

```json
{
  "version": 1,
  "rules": [
    { "bundle_id": "com.1password.1password" },
    { "name": "keychain" }
  ]
}
```

A window is blocked when any rule matches it: `bundle_id` matches the owning
app's identifier exactly (case-insensitive — the robust default, since bundle
ids are stable and unspoofable), or `name` matches its app name as a
case-insensitive substring (handy for a quick edit). `squill doctor` prints
the active rules; a blocked capture exits `6` (the MCP `capture` tool returns
error `type: "blocked"`); every refusal and redaction is audit-logged.

**Know the boundary — this is privacy hygiene, not a security control.**
Anything running as you can capture the screen by other means, so the
blocklist defends against an over-eager or prompt-injected agent reaching for
a password manager *through ShotQuill*, not against a determined adversary
with code execution. Two honest limits: a full-screen capture can only be
redacted where windows can be enumerated (macOS and X11; not under Wayland,
which forbids it — the gap is logged as `redact_unavailable` rather than
silently passed through), and an unreadable blocklist file fails *closed*
(captures are refused until you fix it).

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
- **Blocked apps…** — manage the [app blocklist](#app-blocklist) (apps that are
  never captured).
- **Launch at login** — installs a per-user `LaunchAgent`.
- **Flash on capture** (on) and **Sound on capture** (off) — capture feedback.

---

## Troubleshooting

### macOS

**Captures come out black or empty.** macOS is withholding screen content:
grant **Screen Recording** in System Settings → Privacy & Security, then
restart ShotQuill (macOS only applies the grant to freshly launched
processes). For the CLI/MCP, remember the permission is attributed to the
*invoking* app — your terminal or agent host — not to ShotQuill itself;
`squill doctor` reports exactly which grant is missing.

**Hotkeys don't fire while another app is focused.** Grant **Input
Monitoring** (same privacy pane) and restart. ShotQuill's Settings dialog
shows the live status of both permissions, with a jump-to-pane button.

**A hotkey is silently dead.** Another app may own the same combo — macOS
gives no error; the events simply never arrive. Remap it in Settings.

**"ShotQuill can't be opened" on first launch.** That's Gatekeeper on the
ad-hoc-signed direct download — see [Install](#install) for the
right-click → Open / `xattr` fix. The Homebrew cask is not affected.

### Linux

**ShotQuill exits at startup with "needs a system tray".** The Qt application
came up, but no system-tray host is running. GNOME 42+ ships without legacy
tray support — install the **AppIndicator and KStatusNotifierItem Support**
extension and log out / in. KDE, XFCE, MATE, and Cinnamon include a tray by
default. The `squill` CLI and MCP server still work even without a tray.

**Global hotkeys do nothing on Wayland.** Wayland blocks global key grabs by
design (no per-app keyboard listener can see another app's input). ShotQuill
detects this at startup and shows a notification rather than spawning a
silent dead listener. Workarounds: use the tray menu, or bind a
compositor-level shortcut to `squill capture` (full screen → file) in your
desktop's keyboard settings.

**Captures fail with "Wayland blocks out-of-band grabs".** Install
`xdg-desktop-portal` and a backend for your desktop:
`xdg-desktop-portal-gnome`, `-kde`, or `-wlr`. `squill doctor` will report
when the portal is reachable.

**`squill ocr` errors with "Tesseract is not installed" on Linux.** Install the
`tesseract-ocr` package (and language data such as `tesseract-ocr-eng` /
`tesseract-ocr-chi-sim`) from your distribution; `squill doctor` reports OCR as
available once the `tesseract` binary is on `PATH`. macOS uses Apple Vision and
needs no extra install.

**`squill windows` fails with "no EWMH-compatible window manager is running"
(or "cannot connect to the X server").** X11 enumeration reads the window
manager's EWMH properties, so it needs a running, EWMH-compliant WM (virtually
all modern ones are) and a reachable display. Under Wayland it stays
unsupported by design — the compositor refuses to let an app enumerate other
apps' windows. Full-screen and region capture work regardless; smart-capture
degrades to those modes.

**Smart capture's window highlight never appears.** Same reason as above —
without window enumeration the overlay can't outline a window. Drag for a
region or click for full screen instead.

### Audit log

**Which agent captured what?** Read the audit log:

```bash
tail -f ~/Library/Logs/shotquill/audit.log                     # macOS (also in Console.app)
tail -f "${XDG_STATE_HOME:-$HOME/.local/state}/shotquill/audit.log"  # Linux
```

Each JSONL entry records the action, target, destination, and the process
chain that drove it (`via: "cli"` or `"mcp"`); the same line is mirrored to
the unified log / journald, which user-space processes can't rewrite.

**Still stuck?** Run `squill doctor` and attach its output to a
[GitHub issue](https://github.com/wardmos/shotquill/issues).

---

## Privacy

ShotQuill is built to be trustworthy, and it's open source so you can verify it:

- **No keylogging.** The global-hotkey listener only checks for your configured
  shortcut combos; it never records, stores, or forwards keystrokes.
- **OCR is on-device.** Text recognition uses Apple's Vision framework locally —
  nothing is uploaded, and it works with no network connection.
- **Redaction is real.** The mosaic tool rewrites the underlying pixels before
  export, so blurred-out content isn't recoverable from the saved image.
- **Sensitive apps can be blocklisted.** Name a password manager (or any app)
  and ShotQuill refuses to capture its windows and paints it out of full-screen
  shots — for the GUI, CLI, and agents alike. See [App blocklist](#app-blocklist).
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

Python 3.10+ + [PySide6](https://doc.qt.io/qtforpython/) (Qt) for a self-drawn,
cross-platform UI:

| Concern               | macOS                                                 | Linux                                                  |
| --------------------- | ----------------------------------------------------- | ------------------------------------------------------ |
| GUI / editor canvas   | PySide6 (Qt Widgets + Graphics View)                  | same                                                   |
| Screen capture        | ScreenCaptureKit (macOS 14+), `CGWindowList*` fallback | X11: `QScreen.grabWindow`; Wayland: `xdg-desktop-portal` over QtDBus |
| Window enumeration    | `CGWindowList` (always available)                     | X11: EWMH over `python-xlib`; Wayland: by design refuses |
| Global hotkeys        | `pynput` (Quartz event tap; needs Input Monitoring)   | `pynput` X11 listener (no permission needed); Wayland refuses (use compositor shortcuts) |
| Launch at login       | per-user `LaunchAgent`                                | XDG `~/.config/autostart/shotquill.desktop`            |
| Image processing      | Qt (`QImage`)                                          | same                                                   |
| OCR                   | `pyobjc` → Apple Vision                                | `tesseract` CLI (when installed)                       |

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

### Project layout

```
src/shotquill/
├── app.py                # menu-bar app: tray icon, hotkey → capture → output wiring
├── cli.py                # `squill` argument parsing & exit-code contract
├── headless.py           # shared no-GUI capture/OCR core used by cli.py and mcp.py
├── mcp.py                # `squill mcp` — zero-dependency MCP stdio server
├── audit.py / paths.py   # audit trail for programmatic captures; platform dirs
├── config.py / i18n.py   # QSettings-backed prefs; EN/中文 string table
├── imaging.py            # raw capture pixels → QImage
├── capture/              # base.py + macos.py (ScreenCaptureKit), qtgrab.py (X11), wayland.py (portal)
├── hotkeys/              # base.py + macos.py (Quartz tap), linux.py (pynput X11, Wayland-guarded)
├── ocr/                  # base.py interface; macos.py (Apple Vision), linux.py (Tesseract CLI)
├── output/               # saver.py (files), clipboard.py
├── autostart/            # base.py + macos.py (LaunchAgent), linux.py (XDG .desktop)
└── ui/                   # editor, canvas, tools, smart capture overlay, settings, pin
```

Each `tests/test_*.py` mirrors a module above; platform-independent logic is
tested headlessly, and `capture/hotkeys/ocr/autostart` backends hide behind
`base.py` interfaces so a new OS is a new backend, not a UI rewrite.

### Platform permissions

**macOS** — on first run, grant these in **System Settings → Privacy & Security**:

- **Screen Recording** — required to capture the screen and enumerate windows.
- **Input Monitoring** — required for the global capture hotkeys to work while
  other apps are focused.

ShotQuill's Settings dialog shows the live status of both permissions, with a
button that jumps straight to the right privacy pane.

**Linux / X11** — no special permission is required: the X server lets every
client read the screen and listen for keys. `xhost`-style restrictions, an
extreme SELinux/AppArmor profile, or a remote session without forwarding can
each break capture; `squill doctor` reports what's missing.

**Linux / Wayland** — capture goes through `xdg-desktop-portal`: the first
capture pops a system dialog asking which screen / window to share, and the
choice is remembered for the session. There is no global-hotkey permission to
grant — Wayland blocks them outright; ShotQuill surfaces this in a
notification instead of failing silently.

---

## Uninstall

### macOS

```bash
brew uninstall --cask shotquill        # Homebrew install
# or just drag /Applications/ShotQuill.app to the Trash (direct download)
```

ShotQuill keeps no hidden state beyond these per-user files — remove them for
a clean slate:

| What                        | Where                                              |
| --------------------------- | -------------------------------------------------- |
| Settings                    | `~/Library/Preferences/com.wardmos.ShotQuill.plist` |
| Launch-at-login agent       | `~/Library/LaunchAgents/com.wardmos.shotquill.plist` (only if enabled in Settings) |
| Blocklist                   | `~/Library/Application Support/shotquill/blocklist.json` |
| Audit log                   | `~/Library/Logs/shotquill/`                        |
| Your screenshots            | `~/Pictures/ShotQuill/` (or your configured folder) — yours to keep |

### Linux

```bash
pipx uninstall shotquill               # pipx install
# or delete the downloaded .AppImage
```

| What                        | Where                                              |
| --------------------------- | -------------------------------------------------- |
| Settings                    | `~/.config/wardmos/ShotQuill.conf` (QSettings INI) |
| Autostart entry             | `~/.config/autostart/shotquill.desktop` (only if enabled in Settings) |
| Blocklist                   | `${XDG_CONFIG_HOME:-~/.config}/shotquill/blocklist.json` |
| Audit log                   | `${XDG_STATE_HOME:-~/.local/state}/shotquill/`     |
| Your screenshots            | `~/Pictures/ShotQuill/` (or your configured folder) — yours to keep |

---

## Roadmap

- [x] Smart (window / region / full-screen) + full-screen capture
- [x] Annotation editor (shapes, text, highlighter, mosaic) + pin-to-screen
- [x] On-device OCR (macOS Vision; Linux Tesseract)
- [x] Hands-free auto save + clipboard
- [x] CLI for scripts & AI agents (`squill capture` / `windows` / `ocr` / `doctor`)
- [x] MCP server, so agents can capture and read the screen over Model Context Protocol
- [x] **Linux / X11 backends — GUI, CLI, and MCP**: menu-bar app via PySide6 +
      XDG autostart, full-screen / region capture via `QScreen.grabWindow`,
      global hotkeys via `pynput`
- [x] **Linux / Wayland CLI + MCP** via `xdg-desktop-portal` (Screenshot portal)
- [x] **Multi-monitor selection** — `squill displays` + `capture --display N`
      (and the matching MCP `list_displays` tool / `display` argument)
- [x] **Linux OCR backend** (Tesseract) — `squill ocr` and the editor's
      extract-text action when the `tesseract` CLI is installed
- [ ] **Linux GUI on Wayland** — global hotkeys need the GlobalShortcuts portal
      (the OS forbids out-of-band key grabs), and the smart-capture overlay
      needs to play nicely with compositor full-screen rules
- [x] **X11 window enumeration** — `squill windows`, smart-capture window
      highlight, and full-screen blocklist redaction, via EWMH over `python-xlib`
      (Wayland forbids enumerating other apps' windows, so it stays unsupported
      there by design)
- [x] **Windows backend** — capture (`QScreen.grabWindow`), window enumeration
      (`capture/windows.py`, user32 `EnumWindows`), global hotkeys, and
      launch-at-login (the per-user `Run` key); on-device OCR via the WinRT
      engine ships behind the optional `windows-ocr` extra
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
