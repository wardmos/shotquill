---
name: visual-checkpoints
description: >-
  Leave a visual checkpoint trail when you are mostly editing code or running
  commands but occasionally need to verify a change actually rendered — a
  frontend page, a desktop app window, a built UI. Use after you make the screen
  show the thing to check: capture the state and OCR-assert that the right thing
  is on screen, so a failed check becomes a replayable trace a human or a
  reviewing AI can open later. Record the verification points, not your edits or
  shell output. Wraps ShotQuill's `session_start` / `session_frame` / `capture` /
  `ocr` MCP tools.
---

# Visual checkpoints

You are a coding agent: most of what you do — editing files, running commands,
reading output — has **no GUI and should not be recorded**. This skill is for the
few moments you actually verify your work *on screen*: the page renders, the app
window shows the new state, the build produced the UI you expected.

It leaves a small, OTel-aligned visual trace of those checkpoints — frames on
disk plus a static HTML filmstrip and an OTLP/JSON projection — so when a check
fails, a reviewer (human or AI) opens the recording and lands on exactly what was
on screen when it broke.

> This is the *coding-agent* recipe. If instead you are **driving** a GUI step by
> step (clicking, typing, navigating), use
> [`flight-recorder`](../flight-recorder/SKILL.md) — it records an action frame
> per step. Here you record only the handful of moments you verify a result.

## The loop

```
session_start  →  (work normally)  →  session_frame + assert at each checkpoint  →  session_end
```

1. **Start once, at the beginning of the task.**
   Call `session_start` with an `agent` name and a `label` for the whole task
   ("add the export button", "fix the login redirect"). Keep the returned
   `conversation_id` — every later call passes it as `session`.

2. **Work normally.** Edit code, run the build, start the app. **Do not record
   any of that** — there is no screen state worth filing, and recording your
   scouting buries the checkpoints that matter.

3. **Checkpoint when you have made the screen show what you need to verify.**
   Once the page/app is displaying the result, call `session_frame` with:
   - `session`: the `conversation_id` from start.
   - `tool`: a short kind for the step — `verify`, `render-check`, `smoke`.
   - `label`: what you are checking, for the reviewer — "signup page after the
     new field", "dashboard renders the chart".
   - `contains` and/or `matches`: the **semantic text that proves it worked**
     (`contains: ["Welcome, Ada"]`, `matches: ["Total: \\$\\d+"]`). Add
     `ignore_case` — OCR casing is noisy.
   - Optionally a target (`app`, `window_id`, `region`, `display`) to frame just
     the app under test instead of your whole desktop.

   Read **`assertion_passed`** in the result to branch. A failed assertion
   **still records the frame** — that is the point: the failing checkpoint is
   marked in the filmstrip and set to error in the trace.

4. **End once, when the task is done (or has failed).**
   Call `session_end` with the `session`. It returns the filmstrip path; point the
   user at it. **Always end**, even on failure — a trail that stops at the broken
   checkpoint is exactly what a reviewer needs.

To hand off, list, or clear sessions afterwards, the same `session_export` /
`session_list` / `session_prune` tools apply — see
[`flight-recorder`](../flight-recorder/SKILL.md) ("After the session").

## Capture to look, session_frame to commit a checkpoint

- Use **`capture`** (or **`ocr`**) when you just need to *see* the screen to
  decide what to do — e.g. read an error. These don't record by default; pass the
  session handle as `capture`'s `session` argument if you want a scouting glance
  filed as an `observation` frame (kept separate from action frames).
- Use **`session_frame`** when you are deliberately *checkpointing* — leaving
  evidence and (usually) an assertion. One checkpoint per claim that matters
  beats a frame after every action.

This is how a *failed test becomes a replayable trace*: assert at the moments
that matter, and the recording itself is the pass/fail evidence.

## What to checkpoint — and what not to

- ✅ After the app/page renders the change you made.
- ✅ After a run/build produces a visible result you can assert on.
- ✅ The failure state, when something doesn't render — assert and let it fail;
  the frame is the bug report.
- ❌ Your edits, file reads, `bash` output, test logs — no GUI, nothing to see.
- ❌ A frame after every action. Checkpoint decisions and outcomes, not steps.

## Privacy

- Blocklist redaction is **on and cannot be turned off** mid-session, and frames
  never leave the machine on their own — but redaction only removes the *known*
  apps the user listed, so a frame is **not** guaranteed free of user content.
  Tell the user a recording is minimized and local, never that it's "safe to
  share". (Same model as [`flight-recorder`](../flight-recorder/SKILL.md) —
  see its Privacy note for the full version.)
- **Narrow the surface**: pass `app` / `window_id` / `region` so each frame is
  just the app under test, not the whole desktop. That is the simplest privacy
  win and makes the trace clearer.

## Setup

Register the server with your MCP host (once):

```bash
claude mcp add shotquill -- squill mcp
```

This needs a **real display and screen-recording permission** on the machine
where you run the app under test — that is what `session_frame` / `capture`
photograph. Fully headless CI is not yet supported; run on a local or headed
runner. If a capture is refused, call `doctor` for the per-platform fix.

## Don't

- Don't record your editing/building/scouting — only the visual checkpoints.
- Don't assert on incidental chrome; assert on rendered, semantic content that
  proves the step worked.
- Don't start a second session inside an unfinished one; one task, one session.
- Don't leave a session open. If you stop early, still call `session_end`.
