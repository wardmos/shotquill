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

- **macOS** — the primary, most complete platform, and the one every other is
  measured against: full menu-bar GUI, two global hotkeys, smart window / region
  / full-screen capture (real pixels even under overlap, via ScreenCaptureKit),
  the annotation editor, hands-free save + clipboard, launch-at-login, window
  enumeration, blocklist redaction, and on-device OCR (Apple Vision) — plus the
  full CLI and MCP server.
- **Linux / X11** — full menu-bar GUI plus CLI / MCP, including window
  enumeration (smart-capture window highlight, `squill window list`, and blocklist
  redaction of full-screen grabs) and on-device OCR via Tesseract when it's
  installed.
- **Linux / Wayland** — CLI / MCP via `xdg-desktop-portal`. Global hotkeys are
  blocked by Wayland by design (use the tray menu, or bind a compositor-level
  shortcut to `squill capture`); the GUI surfaces this loudly instead of failing
  silently.
- **Windows** — full menu-bar GUI plus CLI / MCP: capture, window enumeration
  (Win32), global hotkeys, and launch-at-login (the per-user `Run` key). On-device
  OCR runs on the Windows WinRT engine, installed with the optional `windows-ocr`
  extra (`pip install "shotquill[windows-ocr]"`).

> **Status:** early development — macOS is usable day-to-day; the Linux GUI is
> newly landed and still being smoothed out. Expect rough edges either way.

**Jump to:**
[Highlights](#highlights) ·
[Install](#install) ·
[Usage](#usage) ·
[Scripting & agents (CLI · MCP)](docs/scripting.md) ·
[App blocklist](#app-blocklist) ·
[App allowlist](#app-allowlist) ·
[Configuration](#configuration) ·
[Troubleshooting](#troubleshooting) ·
[Privacy](#privacy) ·
[Tech stack](#tech-stack) ·
[Development](#development) ·
[Packaging](docs/packaging.md) ·
[Uninstall](#uninstall) ·
[Roadmap](#roadmap) ·
[Contributing](#contributing)

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
- **On-device OCR** — pull text out of a shot, fully offline, no network, no API
  key. Recognizes Chinese (Simplified) + English. Apple Vision on macOS,
  Tesseract on Linux (when installed), and the WinRT engine on Windows (via the
  optional `windows-ocr` extra).
- **Scriptable & agent-ready** — a headless CLI
  (`squill capture` / `window list` / `display list` / `ocr` / `diff` /
  `session` / `doctor` / `mcp`, plus `blocklist` / `allowlist` — one path on
  stdout, exit codes as the contract) and a built-in MCP server that gives AI
  agents eyes on your screen. Every programmatic capture is audit-logged.
  See [Scripting & agents](docs/scripting.md).
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
squill desktop install          # add ShotQuill to your app menu (pipx-only step)
shotquill                             # launch the menu-bar app
```

`pipx upgrade shotquill` keeps it current. `pip install --user shotquill` works
too if you prefer pip — in that case the `.desktop` launcher and icon land
under `~/.local/share` automatically, so you can skip the `desktop install`
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
> `squill capture --interactive` (the compositor's own picker frames a window,
> region, or screen).

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

## Scripting & agents

ShotQuill has a headless CLI — `shotquill`, or the short alias `squill` — and a
built-in MCP server, so shell scripts and AI agents can capture, read, and record
the screen without the GUI:

```bash
squill capture --app safari -o shot.png    # capture a window to a file
squill ocr --window-id 42 --contains Login # capture + assert on-screen text (exit 20 if absent)
squill session start --agent builder        # begin a replayable session trace
squill mcp                                 # serve the Model Context Protocol over stdio
```

Run bare it launches the GUI; with a subcommand it stays headless and prints one
path on stdout (warnings on stderr), with exit codes as the contract. It captures
one image (`capture`), reads or asserts on-screen text (`ocr`), or records an
ordered trail of frames an agent leaves behind (`session`) — and the same loop is
exposed to MCP clients as twelve tools.

**→ Full reference: [docs/scripting.md](docs/scripting.md)** — the stdout/exit-code
contract, capture flags (`--json` / `--max-width` / `--deterministic` / `--mask` /
`--reveal`),
OCR assertions, **best-effort PII redaction** (`capture --redact-pii`,
`session frame --scan-pii` / `--redact-pii`, `session export --fail-on-pii` —
OCR the frame and mask or flag likely emails, cards, SSNs before output),
the flight recorder + OpenTelemetry trace export, and the MCP
tools. The exit-code contract is also printed in every `squill … --help`.

---

## App blocklist

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

## App allowlist

The inverse of the blocklist, and a tighter leash. The blocklist names what may
*never* be captured; the allowlist, **when you enable it**, flips the default —
ShotQuill then captures *only* the apps you list and refuses everything else.
It is especially useful for agents driving the CLI or MCP: pin the allowlist to
the one or two apps a task needs and the agent cannot wander off and screenshot
your mail, chats, or desktop. **Disabled by default**, so it never gets in the
way until you ask for it.

Manage it from **Settings → Allowed apps…** (tick the box to turn it on), from
the command line, or by hand-editing the JSON file:

```bash
squill allowlist add --bundle-id com.apple.Terminal
squill allowlist add --name firefox       # app-name substring
squill allowlist enable                    # turn the restriction on
squill allowlist list                      # shows enabled state + rules (--json)
squill allowlist disable                   # back to normal capture
squill allowlist remove --name firefox
```

Enforcement covers the **GUI, CLI, and MCP alike** — the same as the blocklist.
In the GUI, full-screen capture (and the region / full-screen modes of smart
capture) are refused with a tray note, and smart capture only lets you pick a
window that's on the list; non-allowed windows are skipped just like blocklisted
ones.

When the allowlist is **enabled**:

- a window or app capture is refused unless its target is on the list;
- a **whole-screen capture (full-screen, region, or display) is refused
  outright** — its "only these apps" promise cannot be kept for a grab of
  everything, so the caller must target a specific window (`--window-id`) or app
  (`--app`);
- a refused capture exits `6` (the MCP `capture` tool returns error
  `type: "blocked"`), and every refusal is audit-logged as `capture_not_allowed`.

It stacks with the blocklist: a window must be **both** off the blocklist **and**
on the allowlist to be captured. The rule shape is identical to the blocklist
(`bundle_id` exact match, or `name` substring). The file lives next to the
blocklist:

- macOS: `~/Library/Application Support/shotquill/allowlist.json`
- Windows: `%APPDATA%\shotquill\allowlist.json`
- elsewhere: `$XDG_CONFIG_HOME/shotquill/allowlist.json`

```json
{
  "version": 1,
  "enabled": true,
  "rules": [
    { "bundle_id": "com.apple.Terminal" },
    { "name": "firefox" }
  ]
}
```

Two things to know: an allowlist that is **enabled with no rules** allows
nothing — a deliberate full lockdown, surfaced by `squill doctor` and the
editor rather than left as mysterious blanket refusals; and like the blocklist
it fails *closed* — an unreadable file, or a by-id capture on a backend that
cannot enumerate windows to verify the target, is refused rather than passed
through. The same boundary applies: this constrains ShotQuill's own capture
paths against an over-eager or prompt-injected agent, not an adversary with code
execution.

> **For agents:** the allowlist can only be changed from the CLI or the GUI —
> it is deliberately **not** exposed over MCP, so an agent on the leash cannot
> loosen its own. Set it up before handing control over.

---

## Configuration

Open **Settings…** from the menu-bar icon:

- **Language** — English / 中文.
- **Save folder** & **image format** (PNG / JPG).
- **Hotkeys** for both capture modes.
- **Highlight window after** — a delay before the hovered window fully lights up
  in smart capture, lifting its pixels out from under any overlap (off by
  default).
- **Editor finish keys** — the in-editor copy and save keys (Space / Enter by
  default), each with its own enable toggle.
- **Adjust region with arrow keys** (on) — keep a region crop nudgeable in the
  editor until the first annotation lands.
- **Edit in place** (on) — open the editor frameless over the dimmed screen,
  rather than as a normal titled window.
- **Toolbar buttons** — icon and text, icon only, or text only (icon and text by
  default).
- **After capture** — auto-save and/or auto-copy toggles (above).
- **Include mouse pointer** (off) — composite the cursor into captures.
- **Blocked apps…** — manage the [app blocklist](#app-blocklist) (apps that are
  never captured).
- **Allowed apps…** — manage the [app allowlist](#app-allowlist) (when enabled,
  the only apps that *can* be captured; off by default).
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

**`squill window list` fails with "no EWMH-compatible window manager is running"
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
- **PII can be redacted automatically.** Programmatic captures can OCR a frame
  and mask likely personal data — emails, card numbers, SSNs — before it ever
  leaves ShotQuill (`squill capture --redact-pii`, `session frame --scan-pii` /
  `--redact-pii`, `session export --fail-on-pii`). It is best-effort, not a
  guarantee, and runs fully on-device. See
  [Scripting & agents](docs/scripting.md).
- **Sensitive apps can be blocklisted.** Name a password manager (or any app)
  and ShotQuill refuses to capture its windows and paints it out of full-screen
  shots — for the GUI, CLI, and agents alike. See [App blocklist](#app-blocklist).
- **Agents can be put on an allowlist.** Flip the default the other way: enable
  the [app allowlist](#app-allowlist) and ShotQuill captures *only* the apps you
  name, refusing every other window and every whole-screen grab — a tight leash
  for an agent on the CLI or MCP, off by default.
- **No telemetry.** ShotQuill makes no network requests of its own.
- **Programmatic captures are accountable.** Scripts and AI agents using the
  CLI or the MCP server go through the same OS consent as any app — macOS
  attributes Screen Recording to the invoking app, so the permission dialog
  names the real controller — and every programmatic capture leaves an audit
  entry (metadata only, never pixels) in a local JSONL file plus the
  tamper-resistant OS log store. The MCP server is strictly opt-in and, by
  design, returns captures to the agent's model — see
  [Scripting & agents](docs/scripting.md#mcp-server) for what that means.

---

## Tech stack

Python 3.10+ + [PySide6](https://doc.qt.io/qtforpython/) (Qt) for a self-drawn,
cross-platform UI:

| Concern               | macOS                                                 | Linux                                                  | Windows                                                |
| --------------------- | ----------------------------------------------------- | ------------------------------------------------------ | ------------------------------------------------------ |
| GUI / editor canvas   | PySide6 (Qt Widgets + Graphics View)                  | same                                                   | same                                                   |
| Screen capture        | ScreenCaptureKit (macOS 14+), `CGWindowList*` fallback | X11: `QScreen.grabWindow`; Wayland: `xdg-desktop-portal` over QtDBus | `QScreen.grabWindow` (per-window via `user32`) |
| Window enumeration    | `CGWindowList` (always available)                     | X11: EWMH over `python-xlib`; Wayland: by design refuses | `user32` `EnumWindows` (Z-order top-level windows)   |
| Global hotkeys        | `pynput` (Quartz event tap; needs Input Monitoring)   | `pynput` X11 listener (no permission needed); Wayland refuses (use compositor shortcuts) | `pynput` Win32 listener (no permission needed) |
| Launch at login       | per-user `LaunchAgent`                                | XDG `~/.config/autostart/shotquill.desktop`            | per-user `Run` key (`HKCU\…\CurrentVersion\Run`)       |
| Image processing      | Qt (`QImage`)                                          | same                                                   | same                                                   |
| OCR                   | `pyobjc` → Apple Vision                                | `tesseract` CLI (when installed)                       | WinRT `Windows.Media.Ocr` (optional `windows-ocr` extra) |

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

For local packaging smoke builds, see [Packaging](docs/packaging.md).

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
| Allowlist                   | `~/Library/Application Support/shotquill/allowlist.json` |
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
| Allowlist                   | `${XDG_CONFIG_HOME:-~/.config}/shotquill/allowlist.json` |
| Audit log                   | `${XDG_STATE_HOME:-~/.local/state}/shotquill/`     |
| Your screenshots            | `~/Pictures/ShotQuill/` (or your configured folder) — yours to keep |

---

## Roadmap

- [x] Smart (window / region / full-screen) + full-screen capture
- [x] Annotation editor (shapes, text, highlighter, mosaic) + pin-to-screen
- [x] On-device OCR (macOS Vision; Linux Tesseract)
- [x] Hands-free auto save + clipboard
- [x] CLI for scripts & AI agents (`squill capture` / `window list` / `display list` /
      `ocr` / `diff` / `session` / `doctor` / `mcp`, plus `blocklist` / `allowlist`)
- [x] MCP server, so agents can capture and read the screen over Model Context Protocol
- [x] **Linux / X11 backends — GUI, CLI, and MCP**: menu-bar app via PySide6 +
      XDG autostart, full-screen / region capture via `QScreen.grabWindow`,
      global hotkeys via `pynput`
- [x] **Linux / Wayland CLI + MCP** via `xdg-desktop-portal` (Screenshot portal)
- [x] **Multi-monitor selection** — `squill display list` + `capture --display N`
      (and the matching MCP `display_list` tool / `display` argument)
- [x] **Linux OCR backend** (Tesseract) — `squill ocr` and the editor's
      extract-text action when the `tesseract` CLI is installed
- [ ] **Linux GUI on Wayland** — global hotkeys need the GlobalShortcuts portal
      (the OS forbids out-of-band key grabs), and the smart-capture overlay
      needs to play nicely with compositor full-screen rules
- [x] **X11 window enumeration** — `squill window list`, smart-capture window
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
