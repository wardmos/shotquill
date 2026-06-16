# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Turn a computer-use run into a ShotQuill flight-recorder trace.

This is the *reference adapter* for feature two (the agent flight recorder),
with Claude computer use as the reference runtime. It is deliberately thin: it
does **not** reimplement the computer-use agent loop, drive a desktop, or wrap
the Anthropic SDK. It does one thing — at each step of a computer-use run, file
a frame into a ShotQuill recording session so the agent's trajectory becomes a
reviewable, redacted, replayable trace.

How it records
--------------
The adapter shells out to the public ``squill record`` CLI (the same contract
documented in ``skills/flight-recorder/SKILL.md``), not to ShotQuill internals.
That is the seam a third party actually integrates against, and it carries two
properties for free:

* **Redaction stays on.** ``squill record frame`` captures the *live* screen
  through ShotQuill's blocklist path, so a blocklisted app (a password manager,
  say) is masked out of the archived frame. The screenshot the model sees is the
  computer-use harness's own high-fidelity capture; the screenshot that lands in
  the trace is ShotQuill's redacted one. Two captures, two fidelities — see
  decisions.md D3 ④ and D15.
* **The trace is the contract.** Each frame is an ``execute_tool`` span with an
  OTel-aligned manifest entry; ``end`` writes the HTML filmstrip and OTLP/JSON
  projection. Nothing here leaves the machine.

What it records
---------------
Following the SKILL.md rule "don't record yourself reading the screen", the
adapter files an **action** frame only for state-changing computer-use actions
(clicks, typing, key presses, scrolls, drags). Pure observation actions the
model takes to *see* the screen (``screenshot``, ``zoom``, ``cursor_position``,
``mouse_move``, ``wait``) are skipped — recording them would bury the actions
that matter under the agent's scouting. (While a session is active, ShotQuill's
MCP path mirrors those observations automatically as ``observation`` frames; the
CLI path used here simply omits them.)
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Exit code the `squill` CLI uses for a failed OCR assertion (see cli.py). Any
# other non-zero code is a real error (3 = permission, 4 = unsupported, 6 =
# blocklist/allowlist, ...), which the adapter raises on.
EXIT_ASSERTION_FAILED = 20


class RecorderError(RuntimeError):
    """A ``squill record`` invocation failed for a reason that isn't a verdict."""


@dataclass(frozen=True)
class Frame:
    """The (tool, label) an action maps to — what the reviewer reads per step."""

    tool: str
    label: str


def _coord(value: Any) -> str:
    """Render a ``[x, y]`` coordinate as ``(x, y)`` for a human label."""
    if isinstance(value, Sequence) and not isinstance(value, str) and len(value) == 2:
        return f"({value[0]}, {value[1]})"
    return "(?, ?)"


# A rule turns one provider action into the frame it leaves behind. It receives
# the action name (so one builder can serve a whole family, like the click
# variants), the action's input dict, and whether typed text must be kept out of
# labels. Names below follow Anthropic's computer use schema; a different
# provider supplies its own rules (see :class:`ActionMap`).
Rule = Callable[[str, Mapping[str, Any], bool], "Frame"]


def _click(name: str, tool_input: Mapping[str, Any], _redact_typed: bool) -> Frame:
    modifier = tool_input.get("text")  # on click actions, `text` is a modifier key
    prefix = f"{modifier}+" if modifier else ""
    verb = name.replace("_", " ")
    return Frame(tool="click", label=f"{prefix}{verb} at {_coord(tool_input.get('coordinate'))}")


def _drag(_name: str, tool_input: Mapping[str, Any], _redact_typed: bool) -> Frame:
    start = tool_input.get("start_coordinate")
    end = tool_input.get("coordinate")
    where = f"from {_coord(start)} to {_coord(end)}" if start is not None else f"to {_coord(end)}"
    return Frame(tool="drag", label=f"drag {where}")


def _key(_name: str, tool_input: Mapping[str, Any], _redact_typed: bool) -> Frame:
    combo = tool_input.get("text", "")
    return Frame(tool="key", label=f"press {combo}" if combo else "press key")


def _hold_key(_name: str, tool_input: Mapping[str, Any], _redact_typed: bool) -> Frame:
    combo = tool_input.get("text", "")
    duration = tool_input.get("duration")
    held = f"hold {combo}" if combo else "hold key"
    return Frame(tool="key", label=f"{held} for {duration}s" if duration is not None else held)


def _scroll(_name: str, tool_input: Mapping[str, Any], _redact_typed: bool) -> Frame:
    modifier = tool_input.get("text")  # on scroll actions, `text` is a modifier key
    prefix = f"{modifier}+" if modifier else ""
    direction = tool_input.get("scroll_direction", "")
    amount = tool_input.get("scroll_amount", "")
    at = _coord(tool_input.get("coordinate"))
    return Frame(tool="scroll", label=f"{prefix}scroll {direction} {amount} at {at}".strip())


def _type(_name: str, tool_input: Mapping[str, Any], redact_typed: bool) -> Frame:
    # Typed text is kept out of the label by default: the manifest is plain JSON
    # on disk and an agent's keystrokes can be a password or other secret.
    text = str(tool_input.get("text", ""))
    if redact_typed:
        return Frame(tool="type", label=f"type ({len(text)} chars)")
    preview = text if len(text) <= 32 else f"{text[:32]}…"
    return Frame(tool="type", label=f'type "{preview}"')


@dataclass(frozen=True)
class ActionMap:
    """How one computer-use provider's actions become flight-recorder frames.

    Injecting an :class:`ActionMap` is the seam for pointing the recorder at a
    *different* computer-use runtime — a non-Anthropic provider, or your own
    desktop harness — without touching the recorder, the ``squill record``
    contract, or redaction. Writing one is writing a table, not subclassing::

        MY_PROVIDER = ActionMap(
            rules={"tap": _click, "type_text": _type, "scroll": _scroll},
            observations=frozenset({"screenshot", "wait"}),
        )

    ``rules`` maps an action name to a builder ``(name, tool_input, redact_typed)
    -> Frame``. ``observations`` lists perception-only actions that leave no
    frame. An action in neither is skipped — the safe default treats an unknown
    action as an observation rather than recording it blind.
    """

    rules: Mapping[str, Rule]
    observations: frozenset[str] = frozenset()

    def describe(
        self, name: str, tool_input: Mapping[str, Any], *, redact_typed: bool = True
    ) -> Frame | None:
        """Map one action to its frame, or ``None`` to skip it. Pure — no I/O."""
        if name in self.observations:
            return None
        rule = self.rules.get(name)
        if rule is None:
            return None
        return rule(name, tool_input, redact_typed)


# The default map: Anthropic's computer use tool (`computer_20251124` /
# `computer_20250124`); action names follow that tool's schema.
_CLICKS = (
    "left_click",
    "right_click",
    "middle_click",
    "double_click",
    "triple_click",
    "left_mouse_down",
    "left_mouse_up",
)
ANTHROPIC_COMPUTER_USE = ActionMap(
    rules={
        **{name: _click for name in _CLICKS},
        "left_click_drag": _drag,
        "key": _key,
        "hold_key": _hold_key,
        "scroll": _scroll,
        "type": _type,
    },
    observations=frozenset({"screenshot", "zoom", "cursor_position", "mouse_move", "wait"}),
)


def _openai_coord(tool_input: Mapping[str, Any]) -> str:
    """Render OpenAI's separate ``x`` / ``y`` fields as a coordinate label."""
    if "coordinate" in tool_input:
        return _coord(tool_input.get("coordinate"))
    x = tool_input.get("x")
    y = tool_input.get("y")
    return f"({x}, {y})" if x is not None and y is not None else "(?, ?)"


def _openai_click(name: str, tool_input: Mapping[str, Any], _redact_typed: bool) -> Frame:
    button = tool_input.get("button")
    prefix = f"{button} " if button else ""
    verb = name.replace("_", " ")
    return Frame(tool="click", label=f"{prefix}{verb} at {_openai_coord(tool_input)}")


def _openai_point(point: Any) -> str:
    """Render one drag-path point — an ``{x, y}`` object (the real shape) or ``[x, y]``."""
    if isinstance(point, Mapping):
        return _openai_coord(point)
    return _coord(point)


def _openai_drag(_name: str, tool_input: Mapping[str, Any], _redact_typed: bool) -> Frame:
    path = tool_input.get("path")
    if isinstance(path, Sequence) and not isinstance(path, str) and len(path) >= 2:
        return Frame(
            tool="drag", label=f"drag from {_openai_point(path[0])} to {_openai_point(path[-1])}"
        )
    return _drag(_name, tool_input, _redact_typed)


def _openai_keypress(_name: str, tool_input: Mapping[str, Any], _redact_typed: bool) -> Frame:
    keys = tool_input.get("keys")
    if isinstance(keys, Sequence) and not isinstance(keys, str):
        combo = "+".join(str(key) for key in keys)
    else:
        combo = str(keys or tool_input.get("text", ""))
    return Frame(tool="key", label=f"press {combo}" if combo else "press key")


def _openai_scroll(_name: str, tool_input: Mapping[str, Any], _redact_typed: bool) -> Frame:
    scroll_x = tool_input.get("scroll_x")
    scroll_y = tool_input.get("scroll_y")
    if scroll_x is None and scroll_y is None:
        return _scroll(_name, tool_input, _redact_typed)
    label = f"scroll ({scroll_x or 0}, {scroll_y or 0}) at {_openai_coord(tool_input)}"
    return Frame(tool="scroll", label=label)


OPENAI_COMPUTER_USE = ActionMap(
    rules={
        "click": _openai_click,
        "double_click": _openai_click,
        "drag": _openai_drag,
        "keypress": _openai_keypress,
        "scroll": _openai_scroll,
        "type": _type,
    },
    # `move` (cursor move) is perception, not a state change — skip it, as the
    # Anthropic map skips `mouse_move`. Recording every move would spam the trace
    # and trigger a full capture per move.
    observations=frozenset({"screenshot", "wait", "move"}),
)


def describe_action(
    name: str,
    tool_input: Mapping[str, Any],
    *,
    action_map: ActionMap = ANTHROPIC_COMPUTER_USE,
    redact_typed: bool = True,
) -> Frame | None:
    """Map one computer-use action to the frame it should leave behind.

    Convenience over ``action_map.describe(...)``, defaulting to Anthropic's
    computer use tool. Returns a :class:`Frame` for a state-changing action, or
    ``None`` for an observation-only (or unknown) action. Pure: no I/O, no Qt, no
    subprocess — the testable core, and the seam to retarget a different provider
    (pass ``action_map=``). Typed text is kept out of labels unless
    ``redact_typed=False``.
    """
    return action_map.describe(name, tool_input, redact_typed=redact_typed)


class FlightRecorder:
    """Drive a ``squill record`` session over a computer-use run.

    Use it as a context manager around the agent loop; call
    :meth:`record_action` once per executed computer-use tool call and
    :meth:`checkpoint` to assert on the screen at moments that matter::

        with FlightRecorder(agent="computer-use", label="book a flight",
                            target={"app": "Safari"}) as rec:
            ...
            rec.record_action(name, tool_input)   # after executing the action
            assert rec.checkpoint(contains=["Confirmation"])

    ``target`` narrows every frame to one window/region instead of the whole
    desktop — the simplest privacy win (minimize the capture surface). It is a
    mapping of ``record frame`` target flags, e.g. ``{"app": "Safari"}``,
    ``{"window_id": 42}``, ``{"region": "0,0,1280,720"}`` or ``{"display": 0}``.
    """

    def __init__(
        self,
        *,
        agent: str,
        label: str | None = None,
        agent_id: str | None = None,
        target: Mapping[str, Any] | None = None,
        directory: str | Path | None = None,
        dedup: bool = True,
        redact_typed: bool = True,
        action_map: ActionMap = ANTHROPIC_COMPUTER_USE,
        squill: str = "squill",
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._agent = agent
        self._label = label
        self._agent_id = agent_id
        self._target = dict(target or {})
        self._directory = directory
        self._dedup = dedup
        self._redact_typed = redact_typed
        self._action_map = action_map
        self._squill = squill
        self._runner = runner
        self.session_dir: str | None = None
        self.filmstrip_path: str | None = None

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> str:
        """Open the session; returns its directory (the handle for later calls)."""
        argv = [self._squill, "record", "start", "--agent", self._agent]
        if self._label is not None:
            argv += ["--label", self._label]
        if self._agent_id is not None:
            argv += ["--agent-id", self._agent_id]
        if self._directory is not None:
            argv += ["--dir", str(self._directory)]
        result = self._run(argv)
        self.session_dir = result.stdout.strip()
        if not self.session_dir:
            raise RecorderError("`squill record start` did not print a session directory")
        return self.session_dir

    def end(self) -> str | None:
        """Close the session and render its filmstrip + OTLP projection."""
        if self.session_dir is None:
            return None
        result = self._run([self._squill, "record", "end", "--session", self.session_dir, "--json"])
        try:
            self.filmstrip_path = json.loads(result.stdout).get("filmstrip")
        except json.JSONDecodeError:
            self.filmstrip_path = None
        return self.filmstrip_path

    def __enter__(self) -> FlightRecorder:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        # Always close the session, even on failure — a trail that stops at the
        # broken step is exactly what a reviewer needs (SKILL.md).
        self.end()

    # -- recording -----------------------------------------------------------

    def record_action(
        self,
        name: str,
        tool_input: Mapping[str, Any],
        *,
        label: str | None = None,
    ) -> bool:
        """File an action frame for one executed computer-use tool call.

        Call this *after* the action has run, so the freshly captured frame shows
        the resulting screen. Returns ``True`` if a frame was filed, ``False`` if
        the action was observation-only (and thus skipped). ``label`` overrides
        the auto-derived one.
        """
        if self.session_dir is None:
            raise RecorderError(
                "recorder is not started; use it as a context manager or call start()"
            )
        frame = self._action_map.describe(name, tool_input, redact_typed=self._redact_typed)
        if frame is None:
            return False
        self._frame(tool=frame.tool, label=label or frame.label)
        return True

    def checkpoint(
        self,
        *,
        contains: Sequence[str] | None = None,
        matches: Sequence[str] | None = None,
        ignore_case: bool = False,
        label: str | None = None,
        tool: str = "assert",
    ) -> bool:
        """File an asserting frame and return whether the check held.

        OCRs the captured (post-redaction) frame and records the verdict on it,
        so a failed check leaves a replayable frame in the trace. Returns the
        boolean verdict; the frame is recorded either way.
        """
        if not contains and not matches:
            raise ValueError("checkpoint() needs at least one of contains= or matches=")
        if self.session_dir is None:
            raise RecorderError(
                "recorder is not started; use it as a context manager or call start()"
            )
        extra: list[str] = []
        for text in contains or ():
            extra += ["--contains", text]
        for pattern in matches or ():
            extra += ["--matches", pattern]
        if ignore_case:
            extra.append("--ignore-case")
        result = self._frame(
            tool=tool, label=label, extra=extra, allow_codes=(EXIT_ASSERTION_FAILED,)
        )
        return result.returncode == 0

    # -- internals -----------------------------------------------------------

    def _frame(
        self,
        *,
        tool: str,
        label: str | None,
        extra: Sequence[str] = (),
        allow_codes: Sequence[int] = (),
    ) -> subprocess.CompletedProcess[str]:
        argv = [self._squill, "record", "frame", "--session", str(self.session_dir), "--tool", tool]
        if label is not None:
            argv += ["--label", label]
        for flag, value in self._target.items():
            argv += [f"--{flag.replace('_', '-')}", str(value)]
        if self._dedup:
            argv.append("--dedup")
        argv += list(extra)
        return self._run(argv, allow_codes=allow_codes)

    def _run(
        self,
        argv: Sequence[str],
        *,
        allow_codes: Sequence[int] = (),
    ) -> subprocess.CompletedProcess[str]:
        result = self._runner(list(argv), capture_output=True, text=True)
        if result.returncode != 0 and result.returncode not in allow_codes:
            detail = (result.stderr or result.stdout or "").strip()
            raise RecorderError(f"{' '.join(argv)} failed (exit {result.returncode}): {detail}")
        return result
