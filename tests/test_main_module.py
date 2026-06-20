# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the ``python -m shotquill`` entry point (``__main__``).

It routes straight through ``cli.main`` so ``python -m shotquill capture`` is
identical to ``squill capture``, and propagates the CLI's exit code (the
stdout/exit-code contract agents rely on). Running the module under ``runpy``
exercises the ``if __name__ == "__main__"`` block without spawning a process.
"""

from __future__ import annotations

import runpy
import sys

import pytest

from shotquill import cli


def _run_main():
    # Another test (the import smoke) may have already imported the module;
    # drop it so runpy re-executes the entry point cleanly (no "found in
    # sys.modules prior to execution" warning).
    sys.modules.pop("shotquill.__main__", None)
    return runpy.run_module("shotquill", run_name="__main__")


def test_routes_through_cli_main(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "main", lambda: calls.append(True) or 0)
    monkeypatch.setattr(sys, "argv", ["shotquill"])
    with pytest.raises(SystemExit) as exc:
        _run_main()
    assert exc.value.code == 0
    assert calls == [True]


def test_propagates_nonzero_exit_code(monkeypatch):
    monkeypatch.setattr(cli, "main", lambda: 7)
    monkeypatch.setattr(sys, "argv", ["shotquill", "capture"])
    with pytest.raises(SystemExit) as exc:
        _run_main()
    assert exc.value.code == 7
