# Computer use → ShotQuill flight recorder

A **thin reference adapter** that turns a [Claude computer use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool)
run into a ShotQuill flight-recorder trace: a reviewable, redacted, replayable
record of what the agent did on screen, step by step.

It is *not* a framework and it does *not* reimplement the computer-use loop. It
adds one thing — at each step, file a frame into a ShotQuill recording session —
so the agent's trajectory becomes an OTel-aligned trace with a human filmstrip
and an OTLP/JSON projection, all on the local machine.

## Files

| File | What it is |
| --- | --- |
| `shotquill_flight_recorder.py` | **Provider-neutral.** The adapter: `describe_action()` / `ActionMap` map an action to a frame; `FlightRecorder` drives the `squill session` CLI over a run. Does not import any model SDK. |
| `desktop.py` | **Provider-neutral.** Driving the desktop: `Outcome`, the `ComputerExecutor` protocol, and `DryRunExecutor` (real screenshots, logged actions). |
| `agent.py` | **Anthropic-specific.** A runnable Claude computer-use loop that reuses the two modules above. |
| `agent_openai.py` | **OpenAI-specific.** A runnable Responses API / `computer_use_preview` loop with the same recorder and executor seam. |

## Provider split

The structure separates the two halves so each vendor is an addition, not a
refactor:

- **Provider-neutral, reused as-is:** `desktop.py` (execute + capture) and
  `FlightRecorder` (record). Nothing here changes per vendor.
- **Provider-specific, written once per vendor:** the request/response wiring,
  plus the action→frame vocabulary — an injectable `ActionMap`.
  `ANTHROPIC_COMPUTER_USE` follows Claude's schema; `OPENAI_COMPUTER_USE`
  follows OpenAI's Responses API computer action schema:

  ```python
  recorder = FlightRecorder(agent="my-agent", action_map=OPENAI_COMPUTER_USE)
  ```

OpenAI support is implemented by the `OPENAI_COMPUTER_USE` table and the sibling
`agent_openai.py` loop (OpenAI talks to a different endpoint and tool-call shape),
with the recorder, the `squill session` contract, redaction, and `desktop.py`
untouched. An action in neither `rules` nor `observations` is skipped (an unknown
action is treated as an observation, not recorded blind).

Only the loop module is vendor-specific by choice:
[decisions.md D4](../../../shotquill_docs/decisions.md) made Claude computer use
the *reference* runtime for v1; other providers are a deliberate follow-on.

## How it records (and why through the CLI)

The adapter shells out to the public `squill session` CLI — the same contract in
[`skills/flight-recorder/SKILL.md`](../../skills/flight-recorder/SKILL.md) — not
to ShotQuill internals. That buys two things:

- **Two captures, two fidelities.** The screenshot the *model* sees is the
  computer-use harness's own high-fidelity capture. The screenshot that lands in
  the *trace* is a fresh ShotQuill capture, taken through the blocklist path, so
  a blocklisted app (a password manager, say) is masked out of the archive.
- **Only actions, not scouting.** Frames are filed for state-changing actions
  (clicks, typing, keys, scrolls, drags). The screenshots the model takes just to
  *see* the screen (`screenshot`, `zoom`, `cursor_position`, `mouse_move`,
  `wait`) are skipped.

Privacy is minimization plus accountability, **not** a guarantee that frames are
free of user content — the agent's actions and the user's data are the same
pixels. Don't tell users a recording is "safe to share"; it is minimized and
stays local. Pass `--app` to narrow every frame to one window (the simplest
minimization). Typed text is **not** echoed into labels by default, because the
manifest is plain JSON and keystrokes can be secrets.

## Run

```bash
pip install -r requirements.txt  # installs anthropic + openai for the examples
pip install -e ../..          # or `pip install shotquill` — provides the `squill` CLI

export ANTHROPIC_API_KEY=...
python agent.py "open the calculator and compute 2 + 2" --app Calculator

export OPENAI_API_KEY=...
python agent_openai.py "open the calculator and compute 2 + 2" --app Calculator
```

The shipped `DryRunExecutor` captures **real** screenshots but does **not** click
or type — it logs the actions it would perform, so the loop runs end to end and
the recording fills with real frames without driving your machine. Swap in a real
executor (e.g. from
[anthropic-quickstarts](https://github.com/anthropics/anthropic-quickstarts/tree/main/computer-use-demo))
to actually control the desktop. When the run ends, open the printed filmstrip
path, or point an OpenTelemetry backend at `trace.otlp.json` in the session
directory.

## Note on ShotQuill's zero-network invariant

ShotQuill itself makes no network requests. These examples do — they call the
Anthropic or OpenAI API — which is why they live under `examples/` with SDKs as
example-only dependencies, outside the importable `shotquill` package. The core
engine, the `squill` CLI, and the recording path stay local and offline.
