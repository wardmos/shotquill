# Scripting & agents

ShotQuill ships a headless CLI — `shotquill`, or the short alias `squill` — and a
built-in MCP server, so shell scripts and AI agents can capture, read, and record
the screen without the GUI. This page is the reference for that surface: the
stdout/exit-code contract, the capture/OCR flags, the flight recorder, and the
MCP tools.

The exit-code contract is also printed in every `squill … --help`, which is the
authoritative source — it lives next to the code and never drifts.

**On this page:**
[CLI](#command-line) ·
[Exit codes](#exit-codes) ·
[Flight recorder](#flight-recorder-record-a-session) ·
[MCP server](#mcp-server)

The [app blocklist](../README.md#app-blocklist) (apps that are never captured)
applies to the CLI and MCP too; its `squill blocklist` commands are documented
with the feature in the main README.

The [app allowlist](../README.md#app-allowlist) is the inverse leash, made for
exactly this surface: enable it and ShotQuill captures *only* the apps you list,
refusing every other window and every whole-screen grab with exit code `6`. Pin
it to the apps a task needs before handing an agent the CLI or MCP and the agent
cannot screenshot anything else. It is configured with `squill allowlist`
(enable / add / remove / list) — deliberately **not** over MCP, so the agent on
the leash cannot loosen its own.

---

## Command line

Run `squill` bare and it launches the menu-bar app; with a subcommand it stays
headless:

```bash
squill capture                            # full screen → temp file, path on stdout
squill capture --app safari -o shot.png   # front-most matching window
squill capture --region 0,0,800,600 -o -  # stream PNG bytes to a pipe
squill capture --display 1 -o second.png  # one monitor (`squill display list` lists them)
squill capture --json --max-width 1024    # downscaled, JSON metadata on stdout
squill capture --deterministic -o shot.png # byte-stable output for golden tests
squill capture --mask 40,12,180,20 -o shot.png  # black out a rectangle before output
squill capture --reveal 40,12,180,20 -o shot.png # mosaic all but this rectangle
squill capture --redact-pii -o shot.png   # OCR and mask likely PII before output
squill capture --scrolling --auto --region 100,120,900,700 -o page.png # long screenshot
squill capture --scrolling --region 100,120,900,700 -o page.png # manual wheel input
squill window list --json                     # list windows, front-most first
squill display list                           # list monitors and their indexes
squill ocr --app safari                   # screen → on-device OCR, one step
squill ocr --window-id 42 --contains Login # assert text is on screen (exit 20 if not)
squill ocr --app safari --boxes           # each line as 'x,y,w,h<TAB>text' (pixel box)
squill diff base.png new.png              # where two images differ (exit 20 if they do)
squill doctor                             # capability & permission report
```

The parts agents rely on:

- **One path on stdout.** `capture` writes one file and prints exactly one
  absolute path; warnings go to stderr. It never touches the clipboard, and
  defaults to a private temp dir — pass `-o` to keep a shot. `--json` swaps
  the bare path for one JSON object (path, target, size, ambiguity count),
  and `--max-width` downscales before the image reaches a vision model.
- **Stitch a scrollable region into one long capture.** `--scrolling` requires
  `--region X,Y,W,H`; add `--auto` to drive the wheel, or omit it and scroll
  the target by hand while the command samples. `--max-height`,
  `--scroll-interval`, and `--scroll-clicks` bound/tune the run. The stitched
  `CaptureResult` then follows the ordinary pipeline, so `--mask`, `--reveal`,
  `--redact-pii`, `--session`, `--max-width`, and deterministic encoding all
  compose normally. The MCP `capture` tool takes `scrolling` plus the three
  tuning fields and always drives the wheel automatically. Every pair must have
  a reliable overlap; a pointer/scrollbar-sized artifact is tolerated, while a
  gap returns an explicit error instead of a corrupted-looking success.
  Available on macOS, Windows, and X11. Wayland returns `unsupported` until
  ShotQuill has a continuous ScreenCast/PipeWire backend.
- **Byte-stable captures for tests.** `--deterministic` pins the embedded DPI
  and strips PNG timestamp/text chunks (and forces the cursor off), so identical
  pixels always encode to identical bytes — what a golden-image diff or content
  hash needs across machines. The MCP `capture` tool takes the same flag.
- **Mask out a region before output.** `--mask X,Y,W,H` (repeatable) blacks out
  a rectangle — in the captured frame's own logical coordinates — before the
  image reaches a file, a pipe, or a model. A caller-controlled redaction
  layered on the app blocklist: blank a field you know holds a secret. The MCP
  `capture` and `session frame` tools take the same `mask` (as `{x,y,width,height}`
  objects); on a recorded frame the mask also hides the region from the OCR
  assertion, not just the archive.
- **Or reveal only the action.** `--reveal X,Y,W,H` (repeatable) is the inverse:
  it mosaics the *whole* frame and keeps only the given rectangle(s) sharp, so a
  recorded frame shows what the agent did without leaving the rest of the screen
  legible — minimize exposure to just the action. Each mosaic cell keeps the
  average of its source block: original per-pixel detail is omitted, but aggregate
  visual information remains. Treat it as exposure minimization, not guaranteed
  secret redaction; use `--mask` for known sensitive regions. Same coordinates,
  same `reveal` arg on the MCP tools; composes with `mask`.
- **Or let it find the PII for you.** `--redact-pii` OCRs the frame and masks the
  pixels of any text that looks like PII (email, credit card, SSN, IBAN, IPv4,
  phone) before output — you don't have to know the coordinates. It reuses the
  same hardened fill as `--mask`, layers on the blocklist, and applies before
  `--reveal`; on `session frame` the redacted pixels are what gets archived,
  asserted, and scanned. The MCP `capture` and `session_frame` tools take the same
  `redact_pii`. **Best-effort, not a guarantee** — it can only mask what OCR
  reads and the detectors catch; for a field you already know holds a secret,
  `--mask` is the certain tool.
- **OCR reads the screen directly.** `squill ocr --app safari` (or
  `--window-id`, `--region`, or nothing for the full screen) captures and
  recognizes in memory — no file, no pipe. `squill ocr shot.png` and
  `squill ocr -` still read a file or stdin.
- **OCR can assert, not just read.** AI-generated apps have no golden image to
  pixel-diff — every build is new — so the useful check is semantic: did the
  right text render? `squill ocr --window-id 42 --contains "Login"` exits `0` if
  the text is on screen and `20` if it isn't, so CI can tell a failed assertion
  from a broken tool. `--matches REGEX` asserts a pattern, both are repeatable
  (all must hold), and `-i` ignores case (OCR case is noisy). The recognized
  text still prints on stdout; the per-check result goes to stderr. The MCP
  `ocr` tool takes the same `contains`/`matches` and returns a structured
  `passed`.
- **OCR can locate text, not just read it.** `--boxes` adds each line's pixel
  bounding box: stdout becomes `x,y,w,h<TAB>text` (image pixels, top-left
  origin — the same coordinates `--mask` redacts), and any `--contains` /
  `--matches` reports *where* it landed on stderr (`ok: text contains 'Login'
  at 40,12,180,20`). The MCP `ocr` tool takes a `boxes` flag and returns a
  `boxes` array plus a `box` on each located assertion — for highlighting a
  match, or masking it.
- **Compare two images to spot a regression.** `squill diff base.png new.png`
  exits `0` when they're identical and `20` when they differ — so a golden-image
  CI step branches on the exit code the same way it does on an OCR assertion — and
  prints *where* they differ as a pixel box (`changed: x,y,w,h`), or notes a size
  mismatch. `--threshold N` absorbs anti-aliasing/compression noise (default `0` =
  exact, right for lossless PNG); `--json` gives the structured verdict. Either
  argument may be `-` to read one image from stdin. See
  [`skills/golden-image/SKILL.md`](../skills/golden-image/SKILL.md) for the
  capture-deterministic → diff-against-baseline recipe.
- **Permissions follow the invoking app.** macOS attributes Screen Recording to
  whatever launched the CLI (your terminal, an agent host) — the consent dialog
  names the real controller, and `squill doctor` reports what is missing.
- **Programmatic capture activity is audit-logged on a best-effort basis** —
  metadata only, never pixels — to a JSONL file
  (`~/Library/Logs/shotquill/audit.log` on macOS,
  `%LOCALAPPDATA%\shotquill\Logs\audit.log` on Windows, and
  `$XDG_STATE_HOME/shotquill/audit.log` elsewhere). macOS and Linux also mirror
  entries into the OS-managed log store (unified log / journald). Each entry
  records the process chain that drove the capture.

`python -m shotquill` accepts the same subcommands.

### Exit codes

Exit codes are split into two bands so a caller can always tell a broken run
from a negative result:

| Band | Code | Meaning |
| --- | --- | --- |
| ok | `0` | success |
| **errors `1`–`19`** | `1` | other error |
| | `2` | usage |
| | `3` | permission denied |
| | `4` | capability unavailable on this platform/session |
| | `5` | no window or display matched |
| | `6` | blocked by the blocklist, or not on the allowlist |
| | `7` | invalid input (e.g. an image past the size cap) |
| **assertion results `20`+** | `20` | OCR assertion failed |

So `rc == 0` passed, `0 < rc < 20` the tool failed, `rc >= 20` an assertion was
false. The two bands can each grow without collision, and `20` dodges the
shell-reserved codes (126+, 255). Every `--help` prints the current list.

---

## Flight recorder (record a session)

Where `capture` returns one image, `squill session` accumulates a **session** —
an ordered trail of frames an agent leaves behind as it operates the screen, so
a human or a reviewing AI can replay what it did, step by step. Frames are
written to disk (never returned into the agent's context), and the blocklist
redaction stays on the whole time.

```bash
DIR=$(squill session start --agent builder --label "login flow")  # prints the session dir
squill session frame "$DIR" --tool click --label "click submit"
squill session frame "$DIR" --tool type  --label "enter email" --app safari
squill session frame "$DIR" --tool assert --contains "Welcome"  # OCR + assert (exit 20 if absent)
squill session frame "$DIR" --tool click --before   # snapshot before an action…
squill session frame "$DIR" --tool click --after    # …and after, paired for a diff
squill session end "$DIR"                            # prints the HTML filmstrip path
squill session export "$DIR" --fail-on-pii          # bundle into one archive (refuse if PII flagged)
```

- **`start` prints the session directory; thread it back as the session handle.**
  Later commands take it as a positional argument (`session frame <handle>`,
  `session end <handle>`, …). Keeping the handle explicit (rather than an ambient
  "current session") is what makes concurrent agents and CI runs safe — the
  handle also accepts the bare conversation id. Pin a location with `--dir`
  (e.g. a CI artifact path).
- **Each session is a directory**: `manifest.json` (the trace), `frames/NNNN.png`
  (one file per frame), and — written at `end` — `index.html` (a static
  filmstrip for a human) plus `trace.otlp.json` (the same trace as
  [OpenTelemetry GenAI](https://opentelemetry.io/docs/specs/semconv/gen-ai/),
  for a machine). A session is an `invoke_agent` span (`gen_ai.conversation.id`),
  each frame an `execute_tool` span carrying the screenshot as a
  `shotquill.frame.*` event.
- **OTLP export is a file, not a network call.** `trace.otlp.json` is OTLP/JSON
  on disk; ShotQuill never sends it anywhere (it makes no network requests at
  all). To ship a trace to an observability backend, point your own OpenTelemetry
  Collector at the file (the `otlpjson` / `filelog` receivers read it directly) —
  so the egress decision, and the credentials for it, stay yours. The GenAI
  semantic conventions are still experimental; the version the fields track is
  recorded on the trace's resource.
- **Pair a before and after frame around an action.** `session frame … --before`
  snapshots the screen before a step; after the action, `session frame … --after`
  files the result and links the two (they share a `pair_id`; `phase` says which
  is which). Pairs nest like brackets — each `--after` closes the most recent open
  `--before` — and a lone `--after` is an error. The filmstrip renders the two
  halves **side by side** in one block, and outlines the region that changed
  between them, so a reviewer can see *what changed* when the agent acted, not
  just the end state (a frame captured between them keeps its own slot). The MCP
  `session_frame` tool takes the same as `phase: "before" | "after"`.
- **Redaction is on by default and cannot be turned off mid-trace**, so a
  blocklisted app cannot be filed into an archive by an agent that "forgot" to
  mask it. The manifest's `redacted` flag means *blocklist protection was in
  force* — not that the frame is free of user content. Agent actions and user
  pixels are the same pixels; redaction only covers the apps you listed.
- **Captures the agent takes to *see* the screen can be logged too.** Pass a
  session handle to a capture — `capture` with `session: <handle>` over MCP, or
  `squill capture --session <id>` on the CLI — and it also files what it grabbed
  as an *observation* frame (omit the handle and the capture records nothing;
  there is no ambient "current session"). Observation frames
  are kept distinct from deliberate `session frame` *action* frames — dimmed in
  the filmstrip, and attached to the trace's root span rather than masquerading
  as a tool call — so a passive glance never reads as a step.
- **A frame can assert, so a failed test is a replayable trace.** Add
  `--contains TEXT` / `--matches REGEX` (`-i` to ignore case) to `session frame`
  and it OCRs the frame it just captured and records the verdict: a failed
  assertion exits `20`, marks the card in the filmstrip, and sets that step's
  OTLP span to error — while still recording the frame, so the failure is
  replayable. This is where the screenshot backend and the flight recorder meet:
  the failing step of a test *is* a frame in the trace.
- **A frame can flag likely PII, or redact it (best-effort, not a guarantee).**
  Add `--scan-pii` to `session frame` (or `scan_pii: true` on the MCP tool) and it
  OCRs the frame and records which kinds of sensitive value likely appear and how
  many — **kind and count only, never the value** — as a residual-risk flag on
  the frame (e.g. for an export gate). Add `--redact-pii` (or `redact_pii: true`)
  to go further and **mask the matched pixels** before the frame is filed, so the
  redacted frame is what gets archived, asserted, and scanned. Both are
  best-effort — they can only act on what OCR reads and the detectors catch — so
  treat a flagged-but-not-redacted frame as "this probably carries a card
  number", and for a field you already know holds a secret use `--mask`.
- **Bundle a session to share it.** `squill session export <session>` packs the
  manifest, frames, filmstrip, and OTLP trace into one archive (`--format
  tar.gz|zip`, `-o` to choose the path) under a single `<id>/` folder — for a CI
  artifact or a hand-off. `--fail-on-pii` refuses (exit `6`) when any frame still
  carries a `--scan-pii` flag, so a flagged trace isn't shared off the machine by
  accident. The MCP `session_export` tool mirrors it and also reports any residual
  PII in its result.
- `--json` on any of these prints a machine-readable object; session steps are
  audit-logged with `via: "record"` on a best-effort basis.

The MCP server exposes the same loop as `session_start` / `session_frame` /
`session_end` (below). Two recipes layer on top of those tools, by agent shape:
[`skills/flight-recorder/SKILL.md`](../skills/flight-recorder/SKILL.md) for an
agent **driving** a GUI step by step (an action frame per step), and
[`skills/visual-checkpoints/SKILL.md`](../skills/visual-checkpoints/SKILL.md) for
a **coding** agent that mostly edits code and only verifies a result on screen at
a few checkpoints.

---

## MCP server

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

Twelve tools: **capture** (full screen / window by id or app+title / one
monitor by `display` index / region; `scrolling` plus a region drives and stitches
a long capture; returns the image inline — pass `max_width` to downscale and save
context; `save_path` optionally persists; `session` optionally files an
observation frame), **window_list**,
**display_list**, **ocr** (a file, or capture-and-recognize fully in memory so
reading on-screen text costs no image tokens), **diff** (compare two images for
golden-image checks), **doctor**, and the flight-recorder tools **session_start**
/ **session_frame** / **session_end** / **session_export** / **session_list** /
**session_prune** (the CLI `session` recorder above, driven by an agent: frames
go to disk, not into the agent's context). MCP tool names are the CLI `noun verb`
subcommands joined by `_` (`window list` → `window_list`).
Built for agent ergonomics: every tool
declares an `outputSchema` and returns typed `structuredContent` (no
re-parsing JSON out of text), read-only tools are annotated `readOnlyHint`
(and destructive ones `destructiveHint`) so hosts can gate or auto-approve them,
and every in-band error carries a `type` plus a `hint` naming the recovery step
(`no_match` → "call window_list", `permission` → "call doctor", …).

Recorded sessions are also exposed as MCP **resources** (the server declares the
`resources` capability): `resources/list` enumerates each session's `filmstrip`
(HTML), `manifest` (the trace), and `otlp` projection under
`shotquill://session/<id>/<kind>`, and `resources/read` returns the contents —
so a host can read a trace back without shelling out to the CLI.

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
