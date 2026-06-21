---
name: flight-recorder
description: >-
  Leave a reviewable visual trail of what you did on a real screen, and
  checkpoint that the right thing rendered. Use when you are operating a GUI on
  the user's machine (clicking, typing, navigating native or web apps) and a
  human or a reviewing AI should later be able to replay your actions step by
  step — debugging, audits, demos, verifying a build, or "show me what the agent
  did". A frame can also assert on its own text (OCR), so a failed check leaves a
  replayable trace. Wraps ShotQuill's `session_start` / `session_frame` /
  `session_end` tools.
---

# Flight recorder

ShotQuill's `record_*` tools give you the *primitives* — open a session, capture
a frame, close it. This skill is the *recipe*: when to capture, how to label, and
when to stop, so the trail you leave is actually replayable.

Frames are written to disk, **not** returned into your context. Recording does
not let you "see" the screen — use `capture` or `ocr` for that. Recording is for
the reviewer who comes after you.

## The loop

```
session_start  →  session_frame (before/after each key action)  →  session_end
```

1. **Start once, at the beginning of the task.**
   Call `session_start` with an `agent` name and a `label` describing the whole
   task ("reset the user's password", "fill the signup form"). Keep the returned
   `conversation_id` — every later call needs it as `session`.

2. **Frame each meaningful step.**
   Before (or right after) an action that changes what's on screen, call
   `session_frame` with:
   - `session`: the `conversation_id` from start.
   - `tool`: the *kind* of action, in a few words — `click`, `type`,
     `navigate`, `open_app`, `submit`. This is the action's name, not a
     sentence.
   - `label`: a human-readable note for the reviewer — "click the Submit
     button", "after entering the email address".
   - Optionally a capture target (`app`, `window_id`, `region`, `display`) to
     frame just the relevant window instead of the whole screen. Narrowing the
     frame is also the simplest privacy win — capture the app you're driving,
     not the user's whole desktop.

   Capture the **state that matters for review**: the screen just before you act
   (what you were looking at) and/or just after (the result). You do not need a
   frame for every mouse move — one per decision or outcome is the right grain.

   > A `capture` you do to *see* the screen can also be logged as an
   > **observation** frame — pass the session handle as `capture`'s `session`
   > argument (omit it and the capture records nothing). Observation frames are
   > kept separate from the `session_frame` *action* frames you file
   > deliberately — so you can keep a record of what you looked at without it
   > masquerading as an action.

3. **End once, when the task is done (or has failed).**
   Call `session_end` with the `session`. It writes a static `index.html`
   filmstrip and returns its path; point the user at it. **Always end the
   session**, even on failure — a trail that stops at the broken step is exactly
   what a reviewer needs.

## Checkpoint with assertions

A frame can also *check* the screen, not just capture it — give `session_frame` a
`contains` and/or `matches` (regex) and it OCRs the frame it just took and
records the verdict on it. Use this to verify a step actually worked: did the
*Welcome* page render, is the order number on screen, did the error dialog go
away?

```
session_frame  session=<id>  tool="assert"  contains=["Welcome, Ada"]
```

- `contains` is one or more substrings, `matches` one or more regexes; **all
  must hold** for the frame to pass. Add `ignore_case` — OCR casing is noisy.
- Read **`assertion_passed`** in the result to branch (and the per-check
  `assertions` list to see which one failed). On the CLI the exit code carries
  it: `0` passed, `20` failed.
- A failed assertion **still records the frame** — that is the point. The failing
  step is marked in the filmstrip and set to error in the trace, so a reviewer
  opens the recording and lands exactly on what was on screen when it broke.

This is how a *failed test becomes a replayable trace*: assert at the moments
that matter (after the page you expected to load, after the action you expected
to take effect), and the recording itself is the evidence of pass or fail.

Assert on **rendered, semantic content** (text that proves the step worked), not
on incidental chrome. One assertion per checkpoint that matters beats asserting
every frame.

## Labels make the trail readable

The reviewer sees your `tool` + `label` next to each frame, in order. Write them
for a person who wasn't there:

- Good: `tool: "click"`, `label: "click 'Confirm purchase'"`
- Good: `tool: "type"`, `label: "enter the 2FA code from the authenticator"`
- Weak: `tool: "step"`, `label: "frame 3"` — says nothing the timestamp doesn't.

## After the session: share, list, clear

`session_end` closes a session and leaves it on disk. Three more tools manage what
happens to it next — each has an MCP tool and a `squill session …` CLI form.

- **Hand off a single trace** with `session_export`: it bundles one session
  (manifest + frames + filmstrip) into a single archive a reviewer can open
  elsewhere. `format` is `tar.gz` (default) or `zip`. Pass `fail_on_pii: true`
  (CLI `--fail-on-pii`) to **refuse the export** (exit `6`) if any frame still
  carries a best-effort PII flag — so a flagged trace isn't shared off the
  machine by accident. Exporting still does not make the frames "safe": see
  Privacy below.

  ```bash
  squill session export "$DIR" --format zip --fail-on-pii
  ```

- **See what's accumulated** with `session_list`: every session newest-first with
  its id, status, frame count, and size on disk — so you know what's there before
  you export or prune.

- **Cap disk cost** with `session_prune`: delete old **complete** sessions by
  `max_age_days` and/or `max_sessions` (it keeps the newest N). It needs at least
  one bound. Pass `dry_run: true` first to see what *would* go — it reports the
  ids and bytes it would free without deleting:

  ```bash
  squill session prune --max-sessions 20 --dry-run   # preview
  squill session prune --max-sessions 20             # then delete
  ```

  Pruning only ever touches finished sessions, so an open recording is never
  removed out from under you.

## Privacy: what recording does and does not do

- Blocklist redaction is **on by default and cannot be turned off** mid-session,
  so an app the user blocklisted (a password manager, say) won't be filed into
  the archive.
- This is **not** a guarantee the frames are free of user content. Your actions
  and the user's private data are the same pixels — redaction only removes the
  *known* apps the user listed. Don't tell the user the recording is "safe to
  share"; tell them it's minimized and stays local.
- Frames never leave the machine on their own. ShotQuill makes no network
  requests; the session is files on disk until someone moves them.

## If you only have the CLI

The same loop, without an MCP host:

```bash
DIR=$(squill session start --agent builder --label "login flow")
squill session frame "$DIR" --tool click  --label "click submit" --app safari
squill session frame "$DIR" --tool assert --contains "Welcome"  # exit 20 if absent
squill session end "$DIR"     # prints the filmstrip path
```

## Don't

- Don't record yourself reading the screen (a `capture`/`ocr` you did to decide
  what to do) — record the *actions you take* and the *checkpoints you assert*,
  not your own scouting observations. (An assertion frame is a deliberate
  checkpoint, so it belongs in the trace; a glance to decide your next move does
  not.)
- Don't start a second session inside an unfinished one; one task, one session.
- Don't leave a session open. If you stop early, still call `session_end`.
