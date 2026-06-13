---
name: flight-recorder
description: >-
  Leave a reviewable visual trail of what you did on a real screen. Use when you
  are operating a GUI on the user's machine (clicking, typing, navigating native
  or web apps) and a human or a reviewing AI should later be able to replay your
  actions step by step — debugging, audits, demos, or "show me what the agent
  did". Wraps ShotQuill's `record_start` / `record_frame` / `record_end` tools.
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
record_start  →  record_frame (before/after each key action)  →  record_end
```

1. **Start once, at the beginning of the task.**
   Call `record_start` with an `agent` name and a `label` describing the whole
   task ("reset the user's password", "fill the signup form"). Keep the returned
   `conversation_id` — every later call needs it as `session`.

2. **Frame each meaningful step.**
   Before (or right after) an action that changes what's on screen, call
   `record_frame` with:
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

3. **End once, when the task is done (or has failed).**
   Call `record_end` with the `session`. It writes a static `index.html`
   filmstrip and returns its path; point the user at it. **Always end the
   session**, even on failure — a trail that stops at the broken step is exactly
   what a reviewer needs.

## Labels make the trail readable

The reviewer sees your `tool` + `label` next to each frame, in order. Write them
for a person who wasn't there:

- Good: `tool: "click"`, `label: "click 'Confirm purchase'"`
- Good: `tool: "type"`, `label: "enter the 2FA code from the authenticator"`
- Weak: `tool: "step"`, `label: "frame 3"` — says nothing the timestamp doesn't.

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
DIR=$(squill record start --agent builder --label "login flow")
squill record frame --session "$DIR" --tool click --label "click submit" --app safari
squill record end --session "$DIR"     # prints the filmstrip path
```

## Don't

- Don't record yourself reading the screen (a `capture`/`ocr` you did to decide
  what to do) — record the *actions you take*, not your own observations.
- Don't start a second session inside an unfinished one; one task, one session.
- Don't leave a session open. If you stop early, still call `record_end`.
