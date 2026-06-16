# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""The provider-neutral half of the computer-use example: driving the desktop.

This module is the seam that lets the project add another computer-use provider
(OpenAI, say) later without duplicating the desktop side. It knows nothing about
any model vendor — it only executes an action and returns a screenshot. A
per-provider loop (``agent.py`` for Anthropic; a future ``agent_openai.py``)
talks to its vendor's SDK, then reuses these pieces and a
:class:`shotquill_flight_recorder.FlightRecorder` for execution and recording.

Split of concerns:

* **provider-specific** (one module per vendor): build the request, read the
  vendor's tool-call shape, build the vendor's tool-result/screenshot reply, and
  the action-name vocabulary (the ``ActionMap`` in ``shotquill_flight_recorder``).
* **provider-neutral** (here + the recorder): execute an action on the desktop,
  capture a redacted frame, file it into the trace.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class Outcome:
    """What an executed action returns to the model: a screenshot and/or text."""

    screenshot_png: bytes | None = None
    text: str | None = None
    error: str | None = None


class ComputerExecutor(Protocol):
    """Executes a single computer-use action and reports the result.

    Implement this against your own desktop harness. ``tool_input`` is the
    provider's computer tool input dict (e.g. ``{"action": ..., "coordinate":
    ...}``). It is provider-neutral: the same executor serves any vendor's loop.
    """

    def execute(self, tool_input: Mapping[str, Any]) -> Outcome: ...


class DryRunExecutor:
    """A placeholder executor: screenshots are real, actions are only logged.

    It uses ``squill capture -o -`` to return a genuine (blocklist-redacted)
    screenshot for every step, so the agent loop progresses and the flight
    recorder fills with real frames — but it never moves the mouse or types.
    Swap it for a real executor to drive an actual machine.
    """

    def __init__(self, *, squill: str = "squill", target: Mapping[str, Any] | None = None) -> None:
        self._squill = squill
        self._target = dict(target or {})

    def execute(self, tool_input: Mapping[str, Any]) -> Outcome:
        action = tool_input.get("action", "")
        if action != "screenshot":
            print(f"  [dry-run] would {action}: {dict(tool_input)}", file=sys.stderr)
        try:
            return Outcome(screenshot_png=self._screenshot())
        except Exception as exc:  # noqa: BLE001 — report any capture failure to the model
            return Outcome(error=f"screenshot failed: {exc}")

    def _screenshot(self) -> bytes:
        argv = [self._squill, "capture", "-o", "-"]
        for flag, value in self._target.items():
            argv += [f"--{flag.replace('_', '-')}", str(value)]
        result = subprocess.run(argv, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(
                (result.stderr or b"").decode().strip() or f"exit {result.returncode}"
            )
        return result.stdout
