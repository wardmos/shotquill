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

# --- pure assertion core ----------------------------------------------------

_LINES = ["Login", "Welcome back, Ada", "Order #41 placed"]


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


# --- CLI: assertion -> exit code --------------------------------------------


class _Recognizer:
    def recognize(self, image):
        return list(_LINES)


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
