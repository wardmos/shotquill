<p align="center">
  <img src="https://raw.githubusercontent.com/wardmos/shotquill/main/packaging/macos/icon.png" alt="ShotQuill icon" width="128" height="128">
</p>

<h1 align="center">ShotQuill</h1>

<p align="center">
  A fast, privacy-respecting screenshot &amp; annotation tool for macOS &mdash; with Linux and Windows GUI plus cross-platform CLI/MCP support.
</p>

<p align="center">
  <a href="https://github.com/wardmos/shotquill/actions/workflows/ci.yml"><img src="https://github.com/wardmos/shotquill/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/shotquill/"><img src="https://img.shields.io/pypi/v/shotquill.svg" alt="PyPI version"></a>
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-blue" alt="Platform: macOS | Linux | Windows">
  <img src="https://img.shields.io/badge/python-3.10+-blue" alt="Python 3.10+">
  <a href="https://github.com/wardmos/shotquill/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-green" alt="License: Apache 2.0"></a>
</p>

From hotkey to share-ready image in seconds. Point at a window, drag a precise
region, or capture every display—then annotate, pixelate sensitive areas,
extract text, copy, save, or pin the result without breaking your flow.

- **Everything you expect from a screenshot app** — capture a window, region,
  screen, or every display; then annotate, pixelate, extract text, copy, save,
  or pin the result, with auto-save and auto-copy when you want them.
- **CLI and MCP support built in** — automate capture, OCR, diffs, and replayable
  sessions with the `squill` CLI, or give AI agents controlled screen access
  through the built-in MCP server.
- **App blocklists and allowlists** — keep sensitive apps out of every capture,
  or restrict capture to approved apps only, with the same rules enforced across
  the GUI, CLI, and MCP server.

**Jump to:**
[Highlights](#highlights) ·
[Platforms](#platform-support) ·
[Install](#install) ·
[Usage](#usage) ·
[Scripting & agents (CLI · MCP)](#scripting--agents) ·
[App blocklist](#app-blocklist) ·
[App allowlist](#app-allowlist) ·
[Configuration](#configuration) ·
[Troubleshooting](#troubleshooting) ·
[Privacy](#privacy) ·
[Tech stack](#tech-stack) ·
[Development](#development) ·
[Packaging](https://github.com/wardmos/shotquill/blob/main/docs/packaging.md) ·
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
- **Configurable after-capture flow** — open the annotation editor by default, or
  make captures hands-free by auto-saving, auto-copying, or both.
- **Annotation editor** — rectangles and ellipses with outline or translucent
  highlight styles, arrows, lines, freehand pen, highlighter, text, and **mosaic
  pixelation** for visual obfuscation. Mosaic
  removes the original per-pixel detail from the exported image but retains
  block-average information; use the solid-fill CLI / blocklist controls for
  high-risk secrets instead.
- **On-device OCR** — pull text out of a shot, fully offline, no network, no API
  key. macOS requests Simplified Chinese and English from Apple Vision; Linux
  uses whichever matching Tesseract language packs are installed. Experimental
  Windows OCR uses the user's installed WinRT OCR languages and requires the
  optional `windows-ocr` extra in a pip install.
- **Scriptable & agent-ready** — a headless CLI
  (`squill capture` / `window list` / `display list` / `ocr` / `diff` /
  `session` / `doctor` / `mcp`, plus `blocklist` / `allowlist` — each command
  documents its stdout, with exit codes as the contract) and a built-in MCP
  server that gives AI agents eyes on your screen. `capture` prints one output
  path by default; programmatic captures are audit-logged on a best-effort
  basis.
  See [Scripting & agents](https://github.com/wardmos/shotquill/blob/main/docs/scripting.md).
- **Pin to screen** — float an annotated shot on top of the desktop for reference;
  drag to move, double-click or `Esc` to dismiss.
- **Bilingual UI** — English / 中文, switchable in Settings (defaults to English).
- **Menu-bar resident** — no Dock clutter; optional launch-at-login.

---

## Platform support

| Platform | Core support | Notes |
| --- | --- | --- |
| **macOS 13+** | Full GUI, smart capture, editor, global hotkeys, window enumeration, on-device OCR, CLI / MCP | Primary and most complete platform; ScreenCaptureKit capture on macOS 14+, with a CoreGraphics fallback on macOS 13. |
| **Linux / X11** | Full GUI, editor, CLI / MCP, window enumeration, global hotkeys, blocklist redaction | OCR requires Tesseract and the desired language packs. |
| **Linux / Wayland** | GUI and editor; capture for GUI / CLI / MCP uses `xdg-desktop-portal` and the compositor's picker | No window enumeration by design; global hotkeys require GlobalShortcuts portal support. |
| **Windows** | GUI, editor, CLI / MCP, Win32 window enumeration, global hotkeys, launch at login | Release ZIP is x64 and omits OCR; the experimental WinRT backend requires the `windows-ocr` extra in a pip install. |

---

## Install

### macOS

**Homebrew (recommended):**

```bash
brew install --cask wardmos/tap/shotquill
```

`brew upgrade --cask shotquill` keeps it current. The cask selects the guarded
PKG CLI component, which puts both `shotquill` and `squill` under
`/usr/local/bin` for CLI / MCP use. Direct PKG and Homebrew installs therefore
use the same links rather than installing duplicate CLIs. Because this installs
a system package under `/Applications`, macOS or Homebrew may request
administrator authorization.

**Direct download:** grab the `.pkg` from
[Releases](https://github.com/wardmos/shotquill/releases) — `arm64` for Apple
Silicon, `x86_64` for Intel Macs, or `universal2` if unsure (works on both,
roughly twice the size) — open it and follow Installer. The component page
always installs ShotQuill in `/Applications`; **Command Line Interface** is
selected by default so `shotquill` and `squill` are added under `/usr/local/bin`.
Deselect it for an app-only installation. Each release ships a `.sha256` sidecar
so you can verify the download. Installer choices do not remove components from
an older installation: to change an existing CLI-enabled install to app-only,
use the built-in uninstaller and then reinstall with CLI deselected.

```bash
shasum -a 256 -c ShotQuill-*.pkg.sha256
```

> The default release build contains an **ad-hoc-signed app in an unsigned,
> unnotarized installer** so the developer can stay anonymous. Gatekeeper may
> block the downloaded package. After trying to open it once, go to **System
> Settings → Privacy & Security** and choose **Open Anyway** only if you trust
> the release and its verified checksum.

The component checkbox cannot control authorization by itself. Both
`/Applications` and `/usr/local/bin` are system locations, so macOS decides
whether to request a password, Touch ID, or other administrator approval when
you click **Install**. It may request approval even when the CLI is not selected.

### Linux

Two channels, pick by what you need:

| You want… | Use |
| --- | --- |
| The **menu-bar GUI** + CLI + MCP | **pipx** (or pip) install from PyPI |
| Just the **CLI / MCP** in one single-file launcher | **x86_64 AppImage** from Releases |

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

**AppImage (CLI / MCP only, x86_64):** download the `.AppImage` from
[Releases](https://github.com/wardmos/shotquill/releases), `chmod +x`, run.
It bundles Python + the headless Qt components (no QtWidgets, no GUI) in one
file, but deliberately uses the host's EGL / GL, D-Bus, and xkbcommon runtime
libraries. A typical graphical desktop already has them; a minimal Ubuntu /
Debian system may need `libegl1 libgl1 libdbus-1-3 libxkbcommon0`. Built on
Ubuntu 22.04 → glibc 2.35 floor (Ubuntu 22.04+ / Debian 12+).

Download the matching `.sha256` sidecar and verify it before running:

```bash
sha256sum -c ShotQuill-*.AppImage.sha256
```

**Wayland users** also need `xdg-desktop-portal` plus a portal backend for
your desktop (`xdg-desktop-portal-gnome`, `-kde`, or `-wlr`) — `squill doctor`
will tell you when screenshot or GlobalShortcuts support is missing. **X11
users** need nothing extra.

> **Linux GUI notes.** ShotQuill needs a system tray to run. GNOME 42+ shipped
> without legacy tray support — install the **AppIndicator and KStatusNotifierItem
> Support** extension; KDE, XFCE, MATE, and Cinnamon already include a tray.
> Global hotkeys (`Alt+A`, `Alt+S`) work on X11. On Wayland they use the
> GlobalShortcuts portal when available; otherwise ShotQuill reports the missing
> portal support so you can use the tray menu or bind a compositor-level shortcut.

### Windows

Download `ShotQuill-*-windows-x64.zip` from
[Releases](https://github.com/wardmos/shotquill/releases), unzip it, and run
`ShotQuill.exe` for the tray GUI. The same bundle includes `squill.exe` for the
CLI and MCP server.

Download the matching `.sha256` sidecar and compare the two values before
extracting the ZIP:

```powershell
Get-FileHash .\ShotQuill-*-windows-x64.zip -Algorithm SHA256
Get-Content .\ShotQuill-*-windows-x64.zip.sha256
```

Windows OCR is experimental: its WinRT integration has not yet been validated
against a live engine and it recognizes languages installed in the user's
Windows profile rather than a fixed English / Chinese pair. The required Python
WinRT projections are optional and are not included in the default package or
release ZIP. To test it with a pip install, use:

```powershell
pip install "shotquill[windows-ocr]"
```

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

> **Linux / Wayland**: global hotkeys use the `xdg-desktop-portal`
> GlobalShortcuts interface when your compositor supports it. If not, ShotQuill
> raises a notification so you can use the tray menu, or bind a compositor-level
> shortcut to `squill capture` (full screen) / `squill capture --interactive`
> (the compositor's own picker frames a window, region, or screen).

### What happens after a capture

By default ShotQuill opens the **annotation editor** after a capture. You can make
captures hands-free in Settings → *After capture* by enabling auto-save,
auto-copy, or both:

| Auto-save | Auto-copy | Result                                                       |
| :-------: | :-------: | ------------------------------------------------------------ |
|     ✅     |     ✅     | Saved **and** copied, no editor.                             |
|     ✅     |     —     | Saved only.                                                  |
|     —     |     ✅     | Copied only.                                                 |
|     —     |     —     | Opens the **annotation editor** instead (default).           |

### Annotation editor

When both auto-output toggles are off (or whenever you want to mark a shot up),
the editor opens with a toolbar:

- **Tools:** select, rectangle, ellipse, arrow, line, pen, highlighter, mosaic,
  and text; shape highlighting, color, and stroke width are separate style
  controls, alongside undo / redo.
- **Copy Text** runs OCR on the capture and copies the recognized text.
- **Pin** floats the annotated shot on top of the desktop.

Keyboard:

| Key            | Action                                  |
| -------------- | --------------------------------------- |
| `Space`        | Copy to the clipboard, then close       |
| `Enter`        | Save to your folder, then close         |
| `⌘Z` / `⌘⇧Z` (macOS); `Ctrl+Z` / `Ctrl+Shift+Z` (Linux / Windows) | Undo / redo |
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

Running it bare launches the GUI; with a subcommand it stays headless. `capture`
writes one file and prints its path by default; listing, OCR, diff, doctor, and
session commands emit their documented text or JSON instead. Warnings go to
stderr, and exit codes are the contract. The same capture / read / record loop
is exposed to MCP clients as twelve tools.

A typical JSON-style MCP host configuration is:

```json
{
  "mcpServers": {
    "shotquill": { "command": "squill", "args": ["mcp"] }
  }
}
```

Some hosts use TOML or their own settings UI, but the command and argument stay
the same.

**→ Full reference: [docs/scripting.md](https://github.com/wardmos/shotquill/blob/main/docs/scripting.md)** — the stdout/exit-code
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
app's identifier exactly (case-insensitive — usually more stable than its
display name, but still matching metadata rather than a verified security
identity), or `name` matches its app name as a case-insensitive substring
(handy for a quick edit). `squill doctor` prints
the active rules; a blocked capture exits `6` (the MCP `capture` tool returns
error `type: "blocked"`); refusals and redactions are audit-logged on a best-effort basis.

**Know the boundary — this is privacy hygiene, not a security control.**
Anything running as you can capture the screen by other means, so the
blocklist defends against an over-eager or prompt-injected agent reaching for
a password manager *through ShotQuill*, not against a determined adversary
with code execution. Two honest limits: a full-screen capture can only be
redacted where windows can be enumerated (macOS and X11; not under Wayland,
which forbids it — blocklist-protected whole-screen / interactive captures are
refused there rather than captured plainly), and an unreadable blocklist file
fails *closed* (captures are refused until you fix it).

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
  `type: "blocked"`), and refusals are audit-logged as `capture_not_allowed` on a best-effort basis.

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
- **Toolbar buttons** — icon and text, icon only, or text only (icon only by
  default).
- **After capture** — auto-save and/or auto-copy toggles (above).
- **Include mouse pointer** (off) — composite the cursor into captures.
- **Blocked apps…** — manage the [app blocklist](#app-blocklist) (apps that are
  never captured).
- **Allowed apps…** — manage the [app allowlist](#app-allowlist) (when enabled,
  the only apps that *can* be captured; off by default).
- **Debug mode** (off) — write detailed local logs for troubleshooting.
- **Launch at login** — installs the platform's per-user startup entry
  (LaunchAgent on macOS, XDG autostart on Linux, the `Run` key on Windows).
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

**A hotkey is silently dead.** Another app may own the same combo — macOS
gives no error; the events simply never arrive. Remap it in Settings. ShotQuill
uses Carbon `RegisterEventHotKey`, so global capture hotkeys do not require Input
Monitoring.

**The installer or ShotQuill is blocked on first launch.** That's Gatekeeper on
the unsigned/unnotarized direct package — see [Install](#install) for the
Privacy & Security override.

### Linux

**ShotQuill exits at startup with "needs a system tray".** The Qt application
came up, but no system-tray host is running. GNOME 42+ ships without legacy
tray support — install the **AppIndicator and KStatusNotifierItem Support**
extension and log out / in. KDE, XFCE, MATE, and Cinnamon include a tray by
default. The `squill` CLI and MCP server still work even without a tray.

**Global hotkeys do nothing on Wayland.** ShotQuill uses the
`xdg-desktop-portal` GlobalShortcuts interface there, because Wayland blocks
classic out-of-band key grabs. If your compositor or portal backend does not
implement GlobalShortcuts, ShotQuill reports that at startup. Workarounds: use
the tray menu, or bind a compositor-level shortcut to `squill capture`
(full screen → file) in your desktop's keyboard settings.

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
apps' windows. Full-screen and region capture work regardless; on Wayland,
smart capture uses the compositor's portal picker instead of ShotQuill's own
window-highlighting overlay.

**Smart capture's window highlight never appears under Wayland.** Same reason
as above — without window enumeration ShotQuill cannot outline a window itself.
The Wayland smart-capture action opens the compositor's picker instead; on
X11/macOS/Windows, ShotQuill keeps its own hover highlight/direct window picking.
Because the portal returns pixels but not the selection's desktop coordinates,
the editor opens as a normal window after a Wayland smart capture.

### Audit log

**Which agent captured what?** Read the audit log:

```text
tail -f ~/Library/Logs/shotquill/audit.log                     # macOS (also in Console.app)
tail -f "${XDG_STATE_HOME:-$HOME/.local/state}/shotquill/audit.log"  # Linux
Get-Content "$env:LOCALAPPDATA\shotquill\Logs\audit.log" -Wait       # Windows PowerShell
```

Each JSONL entry records the action, target, destination, and the process
chain that drove it (`via: "cli"` or `"mcp"`). On macOS and Linux, the same
line is also mirrored to the unified log / journald, which user-space processes
can't rewrite; Windows currently keeps the JSONL log only. Audit logging is
best-effort so a failing log sink never blocks a capture.

**Still stuck?** Run `squill doctor`, then review its output before sharing it:
the report can include local paths, blocklist / allowlist rule labels, display
geometry, and the name of the process responsible for macOS Screen Recording.
Redact anything sensitive before pasting it into a public
[GitHub issue](https://github.com/wardmos/shotquill/issues).

---

## Privacy

ShotQuill is built to be trustworthy, and it's open source so you can verify it:

- **No keylogging.** The global-hotkey listener only checks for your configured
  shortcut combos; it never records, stores, or forwards keystrokes.
- **OCR is on-device.** Text recognition uses Apple Vision on macOS, Tesseract on
  Linux, and the experimental WinRT backend on Windows — nothing is uploaded,
  and the available languages follow each backend as described above.
- **Redaction has explicit boundaries.** Opaque blocklist, `--mask`, and detected
  PII fills overwrite pixels. Mosaic is visual obfuscation: the export omits the
  original per-pixel detail but retains block averages, so do not rely on it to
  hide high-risk secrets.
- **PII can be redacted automatically.** Programmatic captures can OCR a frame
  and mask likely personal data — emails, card numbers, SSNs — before it ever
  leaves ShotQuill (`squill capture --redact-pii`, `session frame --scan-pii` /
  `--redact-pii`, `session export --fail-on-pii`). It is best-effort, not a
  guarantee, and runs fully on-device. See
  [Scripting & agents](https://github.com/wardmos/shotquill/blob/main/docs/scripting.md).
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
  names the real controller — and programmatic captures are audit-logged on a
  best-effort basis (metadata only, never pixels) in a local JSONL file. macOS
  and Linux also mirror entries to the OS-managed log store; Windows currently
  keeps the JSONL log only. The MCP server is strictly opt-in and, by design,
  returns captures to the agent's model — see
  [Scripting & agents](https://github.com/wardmos/shotquill/blob/main/docs/scripting.md#mcp-server)
  for what that means.

---

## Tech stack

Python 3.10+ + [PySide6](https://doc.qt.io/qtforpython/) (Qt) for a self-drawn,
cross-platform UI:

| Concern               | macOS                                                 | Linux                                                  | Windows                                                |
| --------------------- | ----------------------------------------------------- | ------------------------------------------------------ | ------------------------------------------------------ |
| GUI / editor canvas   | PySide6 (Qt Widgets + Graphics View)                  | same                                                   | same                                                   |
| Screen capture        | ScreenCaptureKit (macOS 14+), `CGWindowList*` fallback | X11: `QScreen.grabWindow`; Wayland: `xdg-desktop-portal` over QtDBus | `QScreen.grabWindow` (per-window via `user32`) |
| Window enumeration    | `CGWindowList` (always available)                     | X11: EWMH over `python-xlib`; Wayland: by design refuses | `user32` `EnumWindows` (Z-order top-level windows)   |
| Global hotkeys        | Carbon `RegisterEventHotKey` (no Input Monitoring needed) | X11: `pynput`; Wayland: `xdg-desktop-portal` GlobalShortcuts when available | `pynput` Win32 listener (no permission needed) |
| Launch at login       | per-user `LaunchAgent`                                | XDG `~/.config/autostart/shotquill.desktop`            | per-user `Run` key (`HKCU\…\CurrentVersion\Run`)       |
| Image processing      | Qt (`QImage`)                                          | same                                                   | same                                                   |
| OCR                   | `pyobjc` → Apple Vision (Simplified Chinese + English requested) | `tesseract` CLI (installed language packs)             | Experimental WinRT `Windows.Media.Ocr` (user-profile languages; optional `windows-ocr` extra) |

Platform-specific code (capture, hotkeys, OCR, autostart) sits behind small
`base.py` interfaces, so the editor and output layers stay portable and adding a
new OS means implementing those interfaces rather than touching the UI.

---

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

python -m shotquill              # launch the menu-bar app
ruff check src tests             # lint
ruff format --check src tests    # formatting
pytest                           # tests
```

For local packaging smoke builds, see
[Packaging](https://github.com/wardmos/shotquill/blob/main/docs/packaging.md).

> Screen capture, global hotkeys, and full-screen overlays depend on the target
> desktop session, so platform backends need smoke testing on their OS. Pure
> logic and Qt widgets can be developed and tested headlessly with
> `QT_QPA_PLATFORM=offscreen` where appropriate. Window-activation scenarios
> (`tests/test_activation_macos.py`) only run under a real macOS window server —
> the macOS CI leg, or a Mac without `QT_QPA_PLATFORM` set — because the offscreen
> platform performs no activation arbitration at all.

### Project layout

```
src/shotquill/
├── app.py                # menu-bar app: tray icon, hotkey → capture → output wiring
├── cli.py                # `squill` argument parsing & exit-code contract
├── command_spec.py       # single source for CLI commands and MCP tool schemas
├── headless.py           # shared no-GUI capture/OCR core used by cli.py and mcp.py
├── mcp.py                # `squill mcp` — zero-dependency MCP stdio server
├── audit.py / paths.py   # audit trail for programmatic captures; platform dirs
├── record.py / otlp.py   # replayable sessions, filmstrips, archives, and traces
├── pii.py / redact.py    # best-effort PII masks and app-window redaction
├── config.py / i18n.py   # QSettings-backed prefs; EN/中文 string table
├── imaging.py            # raw capture pixels → QImage
├── capture/              # base.py + macos.py, x11.py/qtgrab.py, wayland.py, windows.py
├── hotkeys/              # base.py + macos.py (Carbon), linux.py, wayland.py, windows.py
├── ocr/                  # base.py interface; macos.py, linux.py, windows.py
├── output/               # saver.py (files), clipboard.py
├── autostart/            # base.py + macos.py, linux.py, windows.py
└── ui/                   # editor, canvas, tools, smart capture overlay, settings, pin
```

Platform-independent logic is tested headlessly, and the
`capture/hotkeys/ocr/autostart` backends hide behind
`base.py` interfaces so a new OS is a new backend, not a UI rewrite.

### Platform permissions

**macOS** — on first run, grant this in **System Settings → Privacy & Security**:

- **Screen Recording** — required to capture the screen and enumerate windows.

Global capture hotkeys use Carbon `RegisterEventHotKey`, so they do not require
Input Monitoring. ShotQuill's Settings dialog shows the live status of Screen
Recording, with a button that jumps straight to the right privacy pane.

**Linux / X11** — no special permission is required: the X server lets every
client read the screen and listen for keys. `xhost`-style restrictions, an
extreme SELinux/AppArmor profile, or a remote session without forwarding can
each break capture; `squill doctor` reports what's missing.

**Linux / Wayland** — capture goes through `xdg-desktop-portal`: the first
capture pops a system dialog asking which screen / window to share, and the
choice is remembered for the session. Global hotkeys go through the
GlobalShortcuts portal when the compositor implements it; there is no separate
per-app keylogging-style permission to grant.

---

## Uninstall

### macOS

ShotQuill can inspect the active installation channel and preview everything an
uninstall would change:

```bash
squill uninstall --dry-run
squill uninstall                 # preview, then ask for confirmation
squill uninstall --yes           # skip the prompt in an interactive terminal
```

The same action is available from **Settings → Uninstall ShotQuill…** for a
direct PKG installation. Homebrew installations are handed back to Homebrew and
show the command below instead of allowing the app to delete Brew-owned files:

```bash
brew uninstall --cask shotquill        # Homebrew install
```

The uninstall flow removes only the validated ShotQuill app, its protected
one-shot helper, its two guarded CLI links, the three package receipts that
exist, and its launch-at-login entry. After administrator authorization, the
protected coordinator has already replaced or closed every App-backed process.
It binds the app, helper, and CLI-link identities before authorization, then
rechecks the bundle identifier, code-signature integrity, ownership, filesystem
boundaries, content generation, ACLs, and literal link targets before deleting
anything. Cancelling authorization, or a failure before app removal, reopens
ShotQuill. A partial cleanup after app removal instead shows recovery steps; the
CLI waits and returns the final result. Settings,
blocklist/allowlist rules, logs, recorded sessions, screenshots, and custom
save folders are preserved.

During a PKG-based Homebrew upgrade, a running ShotQuill is reopened by Homebrew and
recreates an enabled launch-at-login entry. If ShotQuill was not running during
the upgrade, open it once afterward to restore that entry from the preserved
setting.

Before the first upgrade from the older DMG-based cask, quit ShotQuill manually,
run `brew upgrade --cask shotquill`, and reopen it afterward. The legacy cask did
not yet contain the automatic quit/reopen coordinator. Do not install a direct
PKG over that still-registered legacy cask. If that mixed state already exists,
upgrade or reinstall the current cask once so Homebrew records the PKG-based
uninstall coordinator, then uninstall normally.

If a current PKG installation reports that its protected helper is missing or
unsafe, reinstall the same or newer ShotQuill PKG first, then use the built-in
uninstaller. Do not execute an unverified helper with `sudo`.

For an older direct-PKG release that never shipped the protected helper,
install the current PKG over it and then use the built-in uninstaller. This is
safer than manually deleting receipt-listed paths, which may have been replaced
by another tool since the older package was installed.

ShotQuill keeps no hidden state beyond these per-user files — remove them for
a clean slate. Headless captures without an explicit output path use a private
`shotquill/` subdirectory under the OS temporary directory; remove it too for
immediate cleanup instead of waiting for the OS to reclaim temporary files.

| What                        | Where                                              |
| --------------------------- | -------------------------------------------------- |
| Settings                    | `~/Library/Preferences/com.wardmos.ShotQuill.plist` |
| Launch-at-login agent       | `~/Library/LaunchAgents/com.wardmos.shotquill.plist` (only if enabled in Settings) |
| Blocklist                   | `~/Library/Application Support/shotquill/blocklist.json` |
| Allowlist                   | `~/Library/Application Support/shotquill/allowlist.json` |
| Audit log                   | `~/Library/Logs/shotquill/`                        |
| Recorded sessions           | `~/Library/Application Support/shotquill/records/` |
| Temporary CLI/MCP captures  | `$TMPDIR/shotquill/` (normally OS-managed)         |
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
| Recorded sessions           | `${XDG_DATA_HOME:-~/.local/share}/shotquill/records/` |
| pipx desktop launcher       | `${XDG_DATA_HOME:-~/.local/share}/applications/shotquill.desktop` (if `squill desktop install` was run) |
| pipx desktop icon           | `${XDG_DATA_HOME:-~/.local/share}/icons/hicolor/scalable/apps/shotquill.svg` (if `squill desktop install` was run) |
| Temporary CLI/MCP captures  | `${TMPDIR:-/tmp}/shotquill/` (normally OS-managed) |
| Your screenshots            | `~/Pictures/ShotQuill/` (or your configured folder) — yours to keep |

### Windows

Delete the unzipped release folder, or uninstall the Python package if you
installed with pip.

| What                        | Where                                              |
| --------------------------- | -------------------------------------------------- |
| Settings                    | `HKCU\Software\wardmos\ShotQuill` (QSettings registry store) |
| Launch-at-login entry       | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` (only if enabled in Settings) |
| Blocklist                   | `%APPDATA%\shotquill\blocklist.json`              |
| Allowlist                   | `%APPDATA%\shotquill\allowlist.json`              |
| Audit/debug logs            | `%LOCALAPPDATA%\shotquill\Logs\`                  |
| Recorded sessions           | `%LOCALAPPDATA%\shotquill\records\`               |
| Temporary CLI/MCP captures  | `%TEMP%\shotquill\` (normally OS-managed)          |
| Your screenshots            | `Pictures\ShotQuill\` (or your configured folder) — yours to keep |

---

## Roadmap

- [ ] Scrolling / long-page capture

Completed work is summarized in [Highlights](#highlights) and the platform
sections above; version-by-version changes are available in
[GitHub Releases](https://github.com/wardmos/shotquill/releases).

---

## Contributing

Issues and pull requests are welcome. Please run `ruff check`, `ruff format`, and
`pytest` before submitting; CI runs the same on Linux, macOS, and Windows.

---

## License

[Apache-2.0](https://github.com/wardmos/shotquill/blob/main/LICENSE). Copyright (C) 2026 wardmos.

ShotQuill bundles Qt via PySide6, which is licensed under the LGPLv3; the
corresponding license notices are included with distributed builds.
