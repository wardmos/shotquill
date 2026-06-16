# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Reference: the **OpenAI** computer-use loop that leaves a ShotQuill trace.

This is the OpenAI sibling to ``agent.py`` (the Anthropic example). It keeps the
same provider-neutral desktop executor and flight recorder, but talks to
OpenAI's Responses API and the ``computer_use_preview`` tool.

Run::

    export OPENAI_API_KEY=...
    python agent_openai.py "open the calculator and compute 2 + 2"

The shipped ``DryRunExecutor`` captures real screenshots but does not click or
type. Swap it for a real ``ComputerExecutor`` implementation to drive the
machine.
"""

from __future__ import annotations

import argparse
import base64
import sys
from collections.abc import Mapping, Sequence
from typing import Any

from desktop import ComputerExecutor, DryRunExecutor, Outcome
from shotquill_flight_recorder import OPENAI_COMPUTER_USE, FlightRecorder

MODEL = "computer-use-preview"
TOOL_TYPE = "computer_use_preview"
ENVIRONMENT = "browser"
TRUNCATION = "auto"


def _as_dict(value: Any) -> dict[str, Any]:
    """Return a plain dict for OpenAI SDK objects or already-plain values."""
    if isinstance(value, dict):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dict(dump(exclude_none=True))
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return dict(to_dict())
    if hasattr(value, "__dict__"):
        return {key: item for key, item in vars(value).items() if not key.startswith("_")}
    return {}


def _get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _data_url(png: bytes) -> str:
    encoded = base64.standard_b64encode(png).decode()
    return f"data:image/png;base64,{encoded}"


def _computer_call_output(call: Any, outcome: Outcome) -> dict[str, Any]:
    """Build the Responses API input item for one computer-use result."""
    call_id = str(_get(call, "call_id") or _get(call, "id"))
    item: dict[str, Any] = {"type": "computer_call_output", "call_id": call_id}
    pending_safety_checks = _get(call, "pending_safety_checks")
    if pending_safety_checks:
        item["acknowledged_safety_checks"] = pending_safety_checks
    if outcome.screenshot_png is not None:
        item["output"] = {"type": "input_image", "image_url": _data_url(outcome.screenshot_png)}
    else:
        item["output"] = {"type": "input_text", "text": outcome.error or outcome.text or ""}
    return item


def _print_text(output: Sequence[Any], step: int) -> None:
    for item in output:
        item_type = _get(item, "type")
        if item_type == "message":
            for content in _get(item, "content", []) or []:
                text = _get(content, "text")
                if text:
                    print(f"[{step}] {text}")
        elif item_type in {"output_text", "text"}:
            text = _get(item, "text")
            if text:
                print(f"[{step}] {text}")


def _computer_calls(output: Sequence[Any]) -> list[Any]:
    return [item for item in output if _get(item, "type") == "computer_call"]


def run(
    task: str,
    *,
    executor: ComputerExecutor,
    recorder: FlightRecorder,
    display_width: int,
    display_height: int,
    max_steps: int,
    environment: str = ENVIRONMENT,
) -> None:
    """Drive the OpenAI computer-use loop, recording each state-changing action."""
    from openai import OpenAI  # imported here so `--help` works without the SDK installed

    client = OpenAI()
    tools = [
        {
            "type": TOOL_TYPE,
            "display_width": display_width,
            "display_height": display_height,
            "environment": environment,
        }
    ]
    response = client.responses.create(
        model=MODEL,
        tools=tools,
        input=task,
        truncation=TRUNCATION,
    )

    for step in range(max_steps):
        output = list(getattr(response, "output", []) or [])
        _print_text(output, step)
        calls = _computer_calls(output)
        if not calls:
            break

        next_input: list[dict[str, Any]] = []
        for call in calls:
            action = _as_dict(_get(call, "action", {}))
            outcome = executor.execute(action)
            recorder.record_action(str(action.get("type", action.get("action", ""))), action)
            next_input.append(_computer_call_output(call, outcome))

        response = client.responses.create(
            model=MODEL,
            tools=tools,
            previous_response_id=response.id,
            input=next_input,
            truncation=TRUNCATION,
        )
    else:
        print(f"reached the {max_steps}-step cap", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("task", help="what the agent should do, in plain language")
    parser.add_argument(
        "--agent", default="openai-computer-use", help="agent name recorded in the trace"
    )
    parser.add_argument("--label", help="label for the whole session (defaults to the task)")
    parser.add_argument("--app", help="narrow capture to this app (substring) — a privacy win")
    parser.add_argument("--display-width", type=int, default=1280)
    parser.add_argument("--display-height", type=int, default=720)
    parser.add_argument("--environment", default=ENVIRONMENT, help="OpenAI computer environment")
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
        action_map=OPENAI_COMPUTER_USE,
    )
    with recorder:
        run(
            args.task,
            executor=executor,
            recorder=recorder,
            display_width=args.display_width,
            display_height=args.display_height,
            max_steps=args.max_steps,
            environment=args.environment,
        )
    if recorder.filmstrip_path:
        print(f"\nfilmstrip: {recorder.filmstrip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
