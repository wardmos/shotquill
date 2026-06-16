# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the computer-use → flight-recorder reference adapter.

The adapter lives under ``examples/`` (it has an Anthropic-SDK dependency and is
not part of the importable ``shotquill`` package), so this file puts it on the
path explicitly. Everything tested here is pure: the action→frame mapping, and
the ``squill record`` command construction driven through an injected fake runner
— no subprocess, no Qt, no network.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples" / "computer_use"))

from agent_openai import _as_dict, _computer_call_output  # noqa: E402 — path set above
from desktop import Outcome  # noqa: E402 — path set above
from shotquill_flight_recorder import (  # noqa: E402 — path set above
    ANTHROPIC_COMPUTER_USE,
    EXIT_ASSERTION_FAILED,
    OPENAI_COMPUTER_USE,
    ActionMap,
    FlightRecorder,
    Frame,
    RecorderError,
    describe_action,
)


class FakeRunner:
    """Stands in for ``subprocess.run``: records argv, returns canned results."""

    def __init__(self, *, stdout: str = "", returncode: int = 0) -> None:
        self.calls: list[list[str]] = []
        self._stdout = stdout
        self._returncode = returncode
        self.results: dict[str, subprocess.CompletedProcess[str]] = {}

    def __call__(self, argv, *, capture_output, text=False):  # noqa: ANN001
        self.calls.append(list(argv))
        # Match the most recent stage by its `record <verb>` word, falling back
        # to the default canned result.
        verb = argv[2] if len(argv) > 2 else ""
        if verb in self.results:
            return self.results[verb]
        return subprocess.CompletedProcess(argv, self._returncode, self._stdout, "")


# --- describe_action: the pure mapping --------------------------------------


@pytest.mark.parametrize("action", ["screenshot", "zoom", "cursor_position", "mouse_move", "wait"])
def test_observation_actions_are_not_recorded(action):
    assert describe_action(action, {}) is None


def test_unknown_action_is_skipped():
    assert describe_action("teleport", {"coordinate": [1, 2]}) is None


def test_left_click_label_has_coordinate():
    frame = describe_action("left_click", {"coordinate": [500, 300]})
    assert frame == Frame(tool="click", label="left click at (500, 300)")


def test_click_with_modifier():
    frame = describe_action("left_click", {"coordinate": [10, 20], "text": "shift"})
    assert frame.tool == "click"
    assert frame.label == "shift+left click at (10, 20)"


def test_drag_includes_start_and_end():
    frame = describe_action("left_click_drag", {"start_coordinate": [1, 2], "coordinate": [3, 4]})
    assert frame == Frame(tool="drag", label="drag from (1, 2) to (3, 4)")


def test_key_label():
    assert describe_action("key", {"text": "ctrl+s"}) == Frame(tool="key", label="press ctrl+s")


def test_hold_key_with_duration():
    frame = describe_action("hold_key", {"text": "shift", "duration": 2})
    assert frame == Frame(tool="key", label="hold shift for 2s")


def test_scroll_label():
    frame = describe_action(
        "scroll", {"coordinate": [5, 6], "scroll_direction": "down", "scroll_amount": 3}
    )
    assert frame.tool == "scroll"
    assert frame.label == "scroll down 3 at (5, 6)"


def test_type_redacts_content_by_default():
    # The bare mapping never echoes typed text — only the char count.
    frame = describe_action("type", {"text": "hunter2-secret"})
    assert frame == Frame(tool="type", label="type (14 chars)")


def test_type_preview_when_not_redacted():
    frame = describe_action("type", {"text": "hello"}, redact_typed=False)
    assert frame == Frame(tool="type", label='type "hello"')


# --- ActionMap: the injectable provider table -------------------------------


def _custom_tap(name, tool_input, _redact_typed):  # noqa: ANN001
    return Frame(tool="tap", label=f"tap {tool_input.get('at')}")


def test_custom_action_map_routes_by_its_own_table():
    provider = ActionMap(
        rules={"tap": _custom_tap},
        observations=frozenset({"snapshot"}),
    )
    assert provider.describe("tap", {"at": "x"}) == Frame(tool="tap", label="tap x")
    assert provider.describe("snapshot", {}) is None  # observation → skipped
    assert provider.describe("left_click", {"coordinate": [1, 2]}) is None  # unknown → skipped


def test_describe_action_accepts_an_injected_map():
    provider = ActionMap(rules={"tap": _custom_tap})
    assert describe_action("tap", {"at": "y"}, action_map=provider) == Frame(
        tool="tap", label="tap y"
    )


def test_default_map_is_anthropic():
    assert describe_action("left_click", {"coordinate": [1, 1]}) == ANTHROPIC_COMPUTER_USE.describe(
        "left_click", {"coordinate": [1, 1]}
    )


def test_openai_action_map_clicks_with_xy_fields():
    frame = OPENAI_COMPUTER_USE.describe("click", {"x": 20, "y": 30, "button": "left"})
    assert frame == Frame(tool="click", label="left click at (20, 30)")


def test_openai_action_map_keypress_list():
    frame = OPENAI_COMPUTER_USE.describe("keypress", {"keys": ["CTRL", "S"]})
    assert frame == Frame(tool="key", label="press CTRL+S")


def test_openai_action_map_drag_path():
    frame = OPENAI_COMPUTER_USE.describe(
        "drag",
        {"path": [[1, 2], [10, 20], [30, 40]]},
    )
    assert frame == Frame(tool="drag", label="drag from (1, 2) to (30, 40)")


def test_openai_observation_is_not_recorded():
    assert OPENAI_COMPUTER_USE.describe("screenshot", {}) is None


def test_recorder_uses_injected_map():
    runner = FakeRunner()
    runner.results["start"] = subprocess.CompletedProcess([], 0, "/tmp/records/conv-2\n", "")
    rec = FlightRecorder(
        agent="cu",
        runner=runner,
        action_map=ActionMap(rules={"tap": _custom_tap}),
    )
    rec.start()
    assert rec.record_action("tap", {"at": "z"}) is True
    frame_argv = runner.calls[-1]
    assert "--tool" in frame_argv and "tap" in frame_argv
    assert "tap z" in frame_argv
    # An action the injected map doesn't know is skipped, not recorded blind.
    assert rec.record_action("left_click", {"coordinate": [1, 1]}) is False


# --- FlightRecorder: command construction -----------------------------------


def _started(runner: FakeRunner, **kwargs) -> FlightRecorder:
    runner.results["start"] = subprocess.CompletedProcess([], 0, "/tmp/records/conv-1\n", "")
    rec = FlightRecorder(agent="cu", runner=runner, **kwargs)
    rec.start()
    return rec


def test_start_parses_session_dir():
    runner = FakeRunner()
    rec = _started(runner, label="book a flight", agent_id="a-1")
    assert rec.session_dir == "/tmp/records/conv-1"
    start_argv = runner.calls[0]
    assert start_argv[:4] == ["squill", "record", "start", "--agent"]
    assert "--label" in start_argv and "book a flight" in start_argv
    assert "--agent-id" in start_argv and "a-1" in start_argv


def test_start_without_dir_output_raises():
    runner = FakeRunner(stdout="   \n")
    with pytest.raises(RecorderError):
        FlightRecorder(agent="cu", runner=runner).start()


def test_record_action_builds_frame_command_with_target_and_dedup():
    runner = FakeRunner()
    rec = _started(runner, target={"app": "Safari"})
    filed = rec.record_action("left_click", {"coordinate": [7, 8]})
    assert filed is True
    frame_argv = runner.calls[-1]
    assert frame_argv[:6] == [
        "squill",
        "record",
        "frame",
        "--session",
        "/tmp/records/conv-1",
        "--tool",
    ]
    assert "click" in frame_argv
    assert "--label" in frame_argv and "left click at (7, 8)" in frame_argv
    assert "--app" in frame_argv and "Safari" in frame_argv
    assert "--dedup" in frame_argv


def test_record_action_skips_observation_without_calling_cli():
    runner = FakeRunner()
    rec = _started(runner)
    before = len(runner.calls)
    assert rec.record_action("screenshot", {}) is False
    assert len(runner.calls) == before  # no `record frame` was issued


def test_record_action_respects_redact_typed_false():
    runner = FakeRunner()
    rec = _started(runner, redact_typed=False)
    rec.record_action("type", {"text": "hello"})
    frame_argv = runner.calls[-1]
    assert '"hello"' in " ".join(frame_argv)


def test_checkpoint_passes_on_exit_zero():
    runner = FakeRunner()
    rec = _started(runner)
    runner.results["frame"] = subprocess.CompletedProcess([], 0, "/tmp/x.png", "")
    assert rec.checkpoint(contains=["Welcome"], ignore_case=True) is True
    frame_argv = runner.calls[-1]
    assert "--contains" in frame_argv and "Welcome" in frame_argv
    assert "--ignore-case" in frame_argv
    assert "--tool" in frame_argv and "assert" in frame_argv


def test_checkpoint_fails_on_assertion_exit_code():
    runner = FakeRunner()
    rec = _started(runner)
    runner.results["frame"] = subprocess.CompletedProcess([], EXIT_ASSERTION_FAILED, "", "")
    assert rec.checkpoint(matches=[r"\d+ items"]) is False


def test_checkpoint_requires_a_check():
    runner = FakeRunner()
    rec = _started(runner)
    with pytest.raises(ValueError):
        rec.checkpoint()


def test_real_error_exit_code_raises():
    runner = FakeRunner()
    rec = _started(runner)
    runner.results["frame"] = subprocess.CompletedProcess([], 3, "", "permission denied")
    with pytest.raises(RecorderError, match="permission denied"):
        rec.record_action("left_click", {"coordinate": [1, 1]})


def test_record_action_before_start_raises():
    rec = FlightRecorder(agent="cu", runner=FakeRunner())
    with pytest.raises(RecorderError):
        rec.record_action("left_click", {"coordinate": [1, 1]})


def test_context_manager_starts_and_ends():
    runner = FakeRunner()
    runner.results["start"] = subprocess.CompletedProcess([], 0, "/tmp/records/conv-9\n", "")
    runner.results["end"] = subprocess.CompletedProcess(
        [], 0, '{"filmstrip": "/tmp/records/conv-9/index.html"}', ""
    )
    with FlightRecorder(agent="cu", runner=runner) as rec:
        assert rec.session_dir == "/tmp/records/conv-9"
    assert rec.filmstrip_path == "/tmp/records/conv-9/index.html"
    verbs = [c[2] for c in runner.calls]
    assert verbs[0] == "start" and verbs[-1] == "end"


# --- OpenAI Responses API helpers -------------------------------------------


class FakeOpenAIObject:
    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        self.__dict__.update(kwargs)


def test_openai_as_dict_accepts_sdk_like_model_dump():
    class ModelDumpObject:
        def model_dump(self, *, exclude_none):  # noqa: ANN001
            assert exclude_none is True
            return {"type": "click", "x": 1, "y": 2}

    assert _as_dict(ModelDumpObject()) == {"type": "click", "x": 1, "y": 2}


def test_openai_computer_call_output_contains_screenshot_data_url():
    call = FakeOpenAIObject(
        call_id="call_1",
        pending_safety_checks=[{"id": "safe_1", "code": "test", "message": "ok"}],
    )
    output = _computer_call_output(call, Outcome(screenshot_png=b"png"))
    assert output["type"] == "computer_call_output"
    assert output["call_id"] == "call_1"
    assert output["acknowledged_safety_checks"] == [
        {"id": "safe_1", "code": "test", "message": "ok"}
    ]
    assert output["output"] == {"type": "input_image", "image_url": "data:image/png;base64,cG5n"}


def test_openai_computer_call_output_falls_back_to_text_error():
    call = {"id": "item_1"}
    output = _computer_call_output(call, Outcome(error="screenshot failed"))
    assert output == {
        "type": "computer_call_output",
        "call_id": "item_1",
        "output": {"type": "input_text", "text": "screenshot failed"},
    }
