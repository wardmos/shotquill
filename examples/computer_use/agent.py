# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Reference: the **Anthropic** computer-use loop that leaves a ShotQuill trace.

This is the provider-specific half of the example — one module per vendor. It
wires :mod:`shotquill_flight_recorder` into a minimal, real computer-use loop
against the Anthropic API. The provider-neutral pieces it reuses live elsewhere,
so adding another vendor (OpenAI, say) means writing a sibling ``agent_<vendor>.py``
that reuses them, not duplicating them:

* :mod:`desktop` — executing an action and capturing a redacted screenshot.
* :class:`shotquill_flight_recorder.FlightRecorder` — filing frames into the trace.
* :class:`shotquill_flight_recorder.ActionMap` — the action→frame vocabulary
  (this loop uses the default ``ANTHROPIC_COMPUTER_USE``).

The only Anthropic-specific code here is the request shape, the tool/beta/model
constants below, and :func:`_tool_result` (the tool-result block shape).

Action execution is delegated to a pluggable :class:`desktop.ComputerExecutor`.
The shipped :class:`desktop.DryRunExecutor` does **not** click or type: it
captures real (redacted) screenshots via ShotQuill so the loop runs end to end
and the trace fills with real frames, while logging the actions it would perform.
Plug in a real executor (e.g. anthropic-quickstarts' computer-use-demo) to drive
the machine.

Run::

    export ANTHROPIC_API_KEY=...
    python agent.py "open the calculator and compute 2 + 2"

Then open the printed filmstrip path in a browser, or point an OTel backend at
``trace.otlp.json`` in the session directory.
"""

from __future__ import annotations

import argparse
import base64
import sys
from typing import Any

from desktop import ComputerExecutor, DryRunExecutor, Outcome
from shotquill_flight_recorder import FlightRecorder

MODEL = "claude-opus-4-8"
# Tool version + beta header for Claude Opus 4.8 (see the computer use tool docs).
COMPUTER_TOOL_TYPE = "computer_20251124"
COMPUTER_USE_BETA = "computer-use-2025-11-24"
MAX_TOKENS = 4096


def _tool_result(tool_use_id: str, outcome: Outcome) -> dict[str, Any]:
    """Build the tool_result block to send back to the model."""
    if outcome.error is not None:
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": [{"type": "text", "text": outcome.error}],
            "is_error": True,
        }
    content: list[dict[str, Any]] = []
    if outcome.screenshot_png is not None:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.standard_b64encode(outcome.screenshot_png).decode(),
                },
            }
        )
    if outcome.text is not None:
        content.append({"type": "text", "text": outcome.text})
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}


def run(
    task: str,
    *,
    executor: ComputerExecutor,
    recorder: FlightRecorder,
    display_width: int,
    display_height: int,
    max_steps: int,
) -> None:
    """Drive the computer-use loop, recording an action frame after each step."""
    import anthropic  # imported here so `--help` works without the SDK installed

    client = anthropic.Anthropic()
    tools = [
        {
            "type": COMPUTER_TOOL_TYPE,
            "name": "computer",
            "display_width_px": display_width,
            "display_height_px": display_height,
            "enable_zoom": True,
        }
    ]
    messages: list[dict[str, Any]] = [{"role": "user", "content": task}]

    for step in range(max_steps):
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            tools=tools,
            betas=[COMPUTER_USE_BETA],
            thinking={"type": "adaptive"},
            messages=messages,
        )
        # Echo the full assistant turn back (thinking + tool_use blocks) — required
        # to continue the conversation on the same model.
        messages.append({"role": "assistant", "content": response.content})

        for block in response.content:
            if block.type == "text":
                print(f"[{step}] {block.text}")

        if response.stop_reason != "tool_use":
            break

        tool_results: list[dict[str, Any]] = []
        for block in response.content:
            if block.type != "tool_use" or block.name != "computer":
                continue
            outcome = executor.execute(block.input)
            # Record the resulting state *after* executing the action. Observation
            # actions (screenshot/zoom/...) return False and leave no action frame.
            recorder.record_action(str(block.input.get("action", "")), block.input)
            tool_results.append(_tool_result(block.id, outcome))

        if not tool_results:
            break
        messages.append({"role": "user", "content": tool_results})
    else:
        print(f"reached the {max_steps}-step cap", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("task", help="what the agent should do, in plain language")
    parser.add_argument("--agent", default="computer-use", help="agent name recorded in the trace")
    parser.add_argument("--label", help="label for the whole session (defaults to the task)")
    parser.add_argument("--app", help="narrow capture to this app (substring) — a privacy win")
    parser.add_argument("--display-width", type=int, default=1280)
    parser.add_argument("--display-height", type=int, default=720)
    parser.add_argument("--max-steps", type=int, default=20, help="cap on agent loop iterations")
    parser.add_argument(
        "--show-typed",
        action="store_true",
        help="preview typed text in frame labels (off by default; typed keys can be secrets)",
    )
    args = parser.parse_args(argv)

    target = {"app": args.app} if args.app else None
    executor = DryRunExecutor(target=target)
    recorder = FlightRecorder(
        agent=args.agent,
        label=args.label or args.task,
        target=target,
        redact_typed=not args.show_typed,
    )
    with recorder:
        run(
            args.task,
            executor=executor,
            recorder=recorder,
            display_width=args.display_width,
            display_height=args.display_height,
            max_steps=args.max_steps,
        )
    if recorder.filmstrip_path:
        print(f"\nfilmstrip: {recorder.filmstrip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
