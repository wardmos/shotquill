---
name: golden-image
description: >-
  Catch unintended visual change by comparing the screen against a saved
  baseline image, pixel for pixel. Use when a UI should stay byte-identical
  across runs — a rendered page, an app window, an exported image — and you want
  a check that fails when *anything* moves, not one that only looks for specific
  text. Capture deterministically, diff against the golden, and a changed region
  (or a size mismatch) is the failure — with the bounding box of what moved.
  Wraps ShotQuill's `capture --deterministic` and `diff` commands.
---

# Golden-image checks

A golden-image (a.k.a. snapshot or visual-regression) check pins what a screen
*should* look like and fails when the live screen drifts from it. You take one
trusted **baseline** image once, and on every later run you capture the same
surface and diff it against the baseline: identical passes, any changed pixels
fail and tell you *where*.

This is the **pixel** check. If instead you want to assert that *specific text
rendered* (the word "Welcome" is on screen, the total matches a regex), that is
an OCR assertion — use [`visual-checkpoints`](../visual-checkpoints/SKILL.md)
(coding agent) or [`flight-recorder`](../flight-recorder/SKILL.md) (driving a
GUI). Golden-image answers a different question: did anything change at all.

> `diff` is a **CLI command**, not an MCP tool — there is no `diff` tool to call
> over MCP. Shell out to `squill diff`. You can still grab the live frame with
> the `capture` MCP tool (or the CLI); just write it to a file and diff the two
> files.

## The loop

```
capture --deterministic  →  (save once as the baseline)
capture --deterministic  →  diff against baseline  →  exit 0 identical / 20 changed
```

1. **Record the baseline, once.** Capture the surface in a *known-good* state and
   keep the file under version control (or wherever your goldens live):

   ```bash
   squill capture --window-id 42 --deterministic -o golden/dashboard.png
   ```

2. **On every later run, capture the same surface the same way** and diff it:

   ```bash
   squill capture --window-id 42 --deterministic -o /tmp/now.png
   squill diff golden/dashboard.png /tmp/now.png
   ```

3. **Read the result.**
   - Exit `0` / `identical` — nothing changed, the check passes.
   - Exit `20` / `changed: x,y,w,h` — pixels differ; the box is the bounding
     rectangle of what moved, in the image's own coordinates.
   - `differ: size WxH vs WxH` (exit `20`, no box) — the two images aren't even
     the same size; almost always your capture target/scale drifted, not the UI.

   Exit `20` is the predicate-result band (the same one OCR assertions use), so
   `diff` slots straight into a test the way an assertion does.

## `--deterministic` is mandatory on both sides

Plain captures are **not** byte-stable: PNGs carry a timestamp and DPI, the
cursor may be composited in, and any of that makes two captures of the *same*
unchanged screen "differ". `--deterministic` pins the embedded DPI, strips the
PNG timestamp/text chunks, and forces the cursor off — so an unchanged screen
diffs as identical.

Use it when you **write the baseline** and on **every comparison capture**. A
baseline taken without it can never match a deterministic capture.

## Capture the same surface, the same size

A diff is only meaningful if the two images frame the same thing. The most
common false failure is a **size mismatch** from an inconsistent target:

- Pin the target: `--window-id` (most stable), or `--region x,y,w,h` for a fixed
  rectangle. `--app` picks the front-most window and can drift if the window
  moved or resized.
- If window size isn't guaranteed, pin it with `--max-width PX` so both captures
  downscale to the same width.
- Capture on the same display / DPI you took the baseline on.

## Threshold: absorb noise, not real change

By default `diff` is exact (threshold 0): a single off-by-one channel counts.
Anti-aliasing, font hinting, and JPEG/compression introduce tiny per-pixel noise
that isn't real change. Raise `--threshold N` (per-channel delta) just enough to
absorb that noise:

```bash
squill diff golden/page.png /tmp/now.png --threshold 8
```

Keep it as low as you can — a high threshold hides the regressions you are
trying to catch. Prefer PNG goldens (lossless) over JPG so you can keep the
threshold near zero.

## Scripting the check

```bash
squill capture --window-id 42 --deterministic -o /tmp/now.png
if squill diff golden/dashboard.png /tmp/now.png --threshold 4 --json; then
  echo "unchanged"
else
  # exit 20 → changed; --json already printed {changed, box, a_size, b_size}
  echo "dashboard drifted — inspect /tmp/now.png against golden/dashboard.png"
fi
```

`--json` prints `{changed, a_size, b_size, box?}` (and `reason: "size differs"`
when the sizes don't match) instead of the human line — parse that to report the
changed region or to fail a CI step on a non-zero, non-20 exit (a real error).

## When a check fails

A failed diff is a *signal*, not a verdict — the UI may have changed on purpose.

- **Intended change** (you updated the UI): re-take the baseline with the same
  deterministic capture and commit the new golden.
- **Unintended change** (regression): the box points at what moved; open
  `/tmp/now.png` next to the golden and fix the cause.
- **Flapping** with no real change: your capture isn't deterministic — check
  `--deterministic` is on both sides, the target is pinned, and the size matches.

## Privacy

- Blocklist redaction is on for captures too — a blocklisted app (a password
  manager, say) is masked out of the frame before it ever reaches a file.
- Goldens and comparison captures are **files on disk**. ShotQuill makes no
  network requests; they stay local until someone moves them. A committed golden
  is shared with everyone who can read the repo — don't baseline a screen that
  shows secrets or user data.
- Narrow the surface (`--window-id` / `--region`) so a golden frames just the UI
  under test, not the whole desktop — clearer diffs and less to leak.

## Don't

- Don't diff non-deterministic captures — you'll chase phantom changes.
- Don't crank `--threshold` to silence a flaky check; fix the capture instead.
- Don't baseline a resizable window without pinning its size — every resize reads
  as a failure.
- Don't commit a golden of a screen with secrets/PII on it.
