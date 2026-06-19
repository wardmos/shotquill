# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""OCR-as-assert tests: the pure text checks plus the CLI and MCP surfaces.

The assertion core (:mod:`shotquill.textassert`) needs no screen — it runs over
plain lines. The CLI/MCP sections drive a fake recognizer so the exit-code and
structured-result contracts are covered without real OCR.
"""

from __future__ import annotations

import io
import json

import pytest

from shotquill import audit, cli, headless, mcp, paths, textassert
from shotquill.ocr.base import TextBox

# --- pure assertion core ----------------------------------------------------

# Boxes carry the same texts as the lines, each on its own row, so the text-only
# and box-aware paths assert identically while the boxes locate the match.
_BOXES = [
    TextBox("Login", 0, 0, 50, 10),
    TextBox("Welcome back, Ada", 0, 20, 170, 10),
    TextBox("Order #41 placed", 0, 40, 160, 10),
]
_LINES = [box.text for box in _BOXES]


def test_contains_pass_and_fail():
    (ok,) = textassert.evaluate(_LINES, contains=("Welcome back",))
    assert ok.kind == "contains" and ok.passed is True
    (no,) = textassert.evaluate(_LINES, contains=("Logout",))
    assert no.passed is False


def test_contains_spans_recognizer_line_breaks():
    # "Login\nWelcome" only matches because lines are joined before the check.
    (check,) = textassert.evaluate(_LINES, contains=("Login\nWelcome back",))
    assert check.passed is True


def test_matches_regex_and_invalid_regex_raises():
    (check,) = textassert.evaluate(_LINES, matches=(r"Order #\d+",))
    assert check.passed is True
    with pytest.raises(ValueError, match="invalid --matches regex"):
        textassert.evaluate(_LINES, matches=("Order #(",))


def test_ignore_case_applies_to_both_kinds():
    assert textassert.evaluate(_LINES, contains=("login",))[0].passed is False
    assert textassert.evaluate(_LINES, contains=("login",), ignore_case=True)[0].passed is True
    assert textassert.evaluate(_LINES, matches=("WELCOME",), ignore_case=True)[0].passed is True


def test_all_passed_is_anded_and_vacuously_true():
    checks = textassert.evaluate(_LINES, contains=("Login", "Logout"))
    assert textassert.all_passed(checks) is False  # one missing fails the lot
    assert textassert.all_passed([]) is True  # no checks -> nothing to fail


def test_describe_reports_status():
    (ok,) = textassert.evaluate(_LINES, contains=("Login",))
    assert textassert.describe(ok) == "ok: text contains 'Login'"
    (no,) = textassert.evaluate(_LINES, contains=("Nope",))
    assert textassert.describe(no).startswith("FAIL:")


# --- pure: locate matches in boxes (evaluate_boxes) -------------------------


def test_evaluate_boxes_locates_a_single_line_match():
    (check,) = textassert.evaluate_boxes(_BOXES, contains=("Welcome back",))
    assert check.passed is True
    assert check.boxes == (_BOXES[1],)
    # describe() appends the union rect when a check carries boxes.
    assert textassert.describe(check) == "ok: text contains 'Welcome back' at 0,20,170,10"


def test_evaluate_boxes_match_spanning_two_lines_unions_their_boxes():
    (check,) = textassert.evaluate_boxes(_BOXES, contains=("Login\nWelcome back",))
    assert check.passed is True
    assert check.boxes == (_BOXES[0], _BOXES[1])
    # Union covers both rows: y 0..30, widest line 170 wide.
    assert textassert.union_rect(check.boxes) == (0, 0, 170, 30)


def test_evaluate_boxes_failed_check_has_no_boxes():
    (check,) = textassert.evaluate_boxes(_BOXES, contains=("Logout",))
    assert check.passed is False
    assert check.boxes == ()
    assert textassert.describe(check) == "FAIL: text contains 'Logout'"


def test_evaluate_boxes_verdicts_match_text_only_evaluate():
    kw = {"contains": ("Login", "Logout"), "matches": (r"Order #\d+",)}
    located = textassert.evaluate_boxes(_BOXES, **kw)
    text_only = textassert.evaluate(_LINES, **kw)
    assert [c.passed for c in located] == [c.passed for c in text_only]


def test_union_rect_is_none_for_empty():
    assert textassert.union_rect(()) is None


# --- CLI: assertion -> exit code --------------------------------------------


class _Recognizer:
    def recognize(self, image):
        return list(_LINES)

    def recognize_boxes(self, image):
        return list(_BOXES)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "audit_log_path", lambda: tmp_path / "audit.log")
    monkeypatch.setattr(audit, "_to_system_log", lambda line: None)
    monkeypatch.setattr(audit, "_caller_chain", lambda: ["pytest"])
    monkeypatch.setattr(headless, "get_recognizer", lambda: _Recognizer())


@pytest.fixture
def png(tmp_path):
    """A tiny real PNG on disk, so `ocr <path>` decodes without a capturer."""
    pytest.importorskip("PySide6")
    from PySide6.QtGui import QImage

    image = QImage(4, 4, QImage.Format.Format_RGB888)
    image.fill(0)
    path = tmp_path / "shot.png"
    assert image.save(str(path), "PNG")
    return str(path)


def test_ocr_without_assertions_just_prints(png, capsys):
    assert cli.main(["ocr", png]) == 0
    assert capsys.readouterr().out.splitlines() == _LINES


def test_ocr_contains_pass_is_zero(png, capsys):
    assert cli.main(["ocr", png, "--contains", "Welcome back"]) == 0
    assert "ok: text contains 'Welcome back'" in capsys.readouterr().err


def test_ocr_contains_fail_is_assertion_exit_code(png, capsys):
    assert cli.main(["ocr", png, "--contains", "Logout"]) == cli._EXIT_ASSERTION_FAILED
    assert "FAIL: text contains 'Logout'" in capsys.readouterr().err


def test_ocr_multiple_contains_all_must_hold(png):
    # One present, one absent -> the whole assertion fails.
    assert (
        cli.main(["ocr", png, "--contains", "Login", "--contains", "Logout"])
        == cli._EXIT_ASSERTION_FAILED
    )


def test_assertion_exit_code_is_in_the_result_band():
    # Assertion failure must sit in the 20+ predicate band, clear of the 1-19
    # error codes, so CI can tell a false assertion from a broken tool.
    assert cli._EXIT_ASSERTION_FAILED >= 20


def test_ocr_matches_and_ignore_case(png):
    assert cli.main(["ocr", png, "--matches", r"Order #\d+"]) == 0
    assert cli.main(["ocr", png, "--contains", "login"]) == cli._EXIT_ASSERTION_FAILED
    assert cli.main(["ocr", png, "--contains", "login", "--ignore-case"]) == 0


def test_ocr_invalid_regex_is_usage_error(png, capsys):
    assert cli.main(["ocr", png, "--matches", "Order #("]) == 2
    assert "invalid --matches regex" in capsys.readouterr().err


# --- MCP: assertion -> structured result ------------------------------------


def _mcp_ocr(arguments: dict) -> dict:
    raw = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ocr", "arguments": arguments},
        }
    )
    fout = io.StringIO()
    assert mcp.serve(stdin=io.StringIO(raw + "\n"), stdout=fout) == 0
    return json.loads(fout.getvalue())["result"]


def test_mcp_ocr_assertions_report_passed(png):
    result = _mcp_ocr({"path": png, "contains": ["Login"], "matches": [r"Order #\d+"]})
    structured = result["structuredContent"]
    assert structured["passed"] is True
    assert [a["passed"] for a in structured["assertions"]] == [True, True]


def test_mcp_ocr_failed_assertion_sets_passed_false(png):
    structured = _mcp_ocr({"path": png, "contains": ["Logout"]})["structuredContent"]
    assert structured["passed"] is False
    assert structured["assertions"][0] == {
        "kind": "contains",
        "pattern": "Logout",
        "passed": False,
    }


def test_mcp_ocr_without_assertions_omits_passed(png):
    structured = _mcp_ocr({"path": png})["structuredContent"]
    assert "passed" not in structured
    assert "assertions" not in structured
    assert structured["lines"] == _LINES


def test_mcp_ocr_invalid_regex_is_invalid_arguments(png):
    result = _mcp_ocr({"path": png, "matches": ["Order #("]})
    assert result["isError"] is True
    assert json.loads(result["content"][0]["text"])["type"] == "invalid_arguments"


# --- boxes surface: CLI --boxes and MCP boxes (D11) -------------------------


def test_cli_boxes_prints_coordinates_then_text(png, capsys):
    assert cli.main(["ocr", png, "--boxes"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "0,0,50,10\tLogin",
        "0,20,170,10\tWelcome back, Ada",
        "0,40,160,10\tOrder #41 placed",
    ]


def test_cli_boxes_locates_passing_assertion(png, capsys):
    assert cli.main(["ocr", png, "--boxes", "--contains", "Welcome back"]) == 0
    assert "ok: text contains 'Welcome back' at 0,20,170,10" in capsys.readouterr().err


def test_cli_without_boxes_keeps_plain_text(png, capsys):
    assert cli.main(["ocr", png]) == 0
    assert capsys.readouterr().out.splitlines() == _LINES  # no coordinates


def test_mcp_boxes_returns_per_line_boxes_and_locates_assertion(png):
    structured = _mcp_ocr({"path": png, "boxes": True, "contains": ["Login"]})["structuredContent"]
    assert structured["boxes"][0] == {
        "text": "Login",
        "x": 0,
        "y": 0,
        "width": 50,
        "height": 10,
    }
    assert structured["assertions"][0]["box"] == {"x": 0, "y": 0, "width": 50, "height": 10}


def test_mcp_without_boxes_omits_boxes_and_assertion_box(png):
    structured = _mcp_ocr({"path": png, "contains": ["Login"]})["structuredContent"]
    assert "boxes" not in structured
    assert "box" not in structured["assertions"][0]
