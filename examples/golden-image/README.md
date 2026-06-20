# Golden-image checks

A thin walkthrough of the [`golden-image`](../../skills/golden-image/SKILL.md)
skill: how an agent or a CI step pins what a screen **should** look like and fails
when the live screen drifts from it — a rendered page, an app window, an exported
image — by capturing deterministically and diffing against a saved baseline.

There is **no new code here**. The integration is two CLI commands —
`squill capture --deterministic` and `squill diff` — wired together. The skill
supplies the *recipe* (when `--deterministic` matters, how to pin the surface,
how to read the verdict); this file shows the wiring and one concrete run.

## Why the CLI (and why this is the *pixel* check)

`diff` is a **CLI command**, not an MCP tool — there is no `diff` tool to call
over MCP — so a golden-image check is a small shell pipeline, not an agent
tool-loop. That makes it the natural shape for a CI step or a pre-commit guard.

It also answers a different question from the recording recipes. The
[`flight-recorder`](../../skills/flight-recorder/SKILL.md) and
[`visual-checkpoints`](../../skills/visual-checkpoints/SKILL.md) skills assert on
**semantic text** (did "Welcome" render, does the total match a regex) via OCR.
Golden-image asserts on **pixels**: did *anything at all* change. Use it when a UI
is supposed to stay byte-identical across runs.

## Record the baseline, once

Capture the surface in a known-good state and keep the file under version control
(or wherever your goldens live). Pin the target so the frame is reproducible:

```bash
squill capture --window-id 42 --deterministic -o golden/dashboard.png
```

`--deterministic` is what makes this work: it pins the embedded DPI, strips the
PNG timestamp/text chunks, and forces the cursor off, so an unchanged screen
captures to identical bytes. A baseline taken without it can never match a
deterministic comparison capture.

## A run, end to end

CI (or an agent) captures the same surface the same way and diffs it:

```bash
squill capture --window-id 42 --deterministic -o /tmp/now.png

if squill diff golden/dashboard.png /tmp/now.png --threshold 4 --json; then
  echo "dashboard unchanged"          # exit 0 → identical
else
  status=$?
  if [ "$status" -eq 20 ]; then
    # {changed:true, box:{x,y,width,height}, a_size, b_size} already printed
    echo "dashboard drifted — inspect /tmp/now.png against golden/dashboard.png"
    exit 1
  fi
  echo "diff errored (status $status)"; exit "$status"   # 2 usage, 7 bad input, …
fi
```

Exit `20` is the predicate-result band — the same one OCR assertions use — so the
check branches on the exit code exactly like an assertion does. `--json` prints
`{changed, a_size, b_size, box?}` (and `reason: "size differs"` when the sizes
don't match) for a machine to parse the changed region.

`--threshold N` absorbs anti-aliasing/compression noise; keep it as low as you
can — a high threshold hides the regressions you're trying to catch. Prefer PNG
goldens (lossless) so you can keep it near zero.

## When the check fails

A failed diff is a *signal*, not a verdict — the UI may have changed on purpose.

- **Intended change** — re-take the baseline with the same deterministic capture
  and commit the new golden.
- **Regression** — the box points at what moved; open the comparison capture next
  to the golden and fix the cause.
- **`differ: size WxH vs WxH`** with no box — the capture target or scale drifted,
  not the UI. Pin `--window-id` / `--region`, and `--max-width` if the window can
  resize.

## Privacy

Blocklist redaction is on for captures too — a blocklisted app is masked out of
the frame before it reaches a file. Goldens and comparison captures are files on
disk; ShotQuill makes no network requests, so they stay local until someone moves
them. A committed golden is shared with everyone who can read the repo — narrow
the surface (`--window-id` / `--region`) and never baseline a screen showing
secrets or user data.
