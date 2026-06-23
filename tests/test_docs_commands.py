# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""The docs advertise a CLI surface; this guards that every `squill <command>`
they invoke is a real registered command, so the prose can't drift away from the
registry (e.g. naming a command that was renamed or never existed)."""

from __future__ import annotations

import re
from pathlib import Path

from shotquill import command_spec

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DOC_FILES = [_REPO_ROOT / "README.md", *sorted((_REPO_ROOT / "docs").glob("*.md"))]

# Only look inside code — fenced blocks and inline-code spans — so prose that
# happens to follow a command name ("`squill session` accumulates …") isn't
# mistaken for a subcommand.
_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_INLINE = re.compile(r"`([^`\n]+)`")
# A `squill <word> [<word>]` invocation: the two leading lowercase tokens are
# enough to resolve a 1- or 2-token command path. Trailing flags/args start with
# `-` or carry `.`/digits, so they don't match `[a-z]+`.
_INVOCATION = re.compile(r"squill\s+([a-z]+)(?:\s+([a-z]+))?")

_PATHS = {cmd.cli_path for cmd in command_spec.REGISTRY}
_LEAF = {p[0] for p in _PATHS if len(p) == 1}  # one-token commands (capture, ocr, …)
_GROUPS = {p[0] for p in _PATHS if len(p) == 2}  # parents of two-token commands


def _code_fragments(text: str):
    yield from _FENCE.findall(text)
    # Inline spans, with fenced blocks stripped so their inner backticks don't skew.
    yield from _INLINE.findall(_FENCE.sub("", text))


def _invocations():
    for doc in _DOC_FILES:
        if not doc.exists():
            continue
        text = doc.read_text(encoding="utf-8")
        for fragment in _code_fragments(text):
            for first, second in _INVOCATION.findall(fragment):
                yield doc.name, first, second


def test_docs_invoke_only_real_commands():
    invocations = list(_invocations())
    assert invocations, "no `squill ...` invocations found to validate"

    bad: list[str] = []
    for doc, first, second in invocations:
        if first in _LEAF:
            continue  # leaf command; any trailing token is an argument, not a path
        if first in _GROUPS:
            # A bare group name is a valid reference to the command family; a
            # following token must name a real subcommand.
            if not second or (first, second) in _PATHS:
                continue
            bad.append(f"{doc}: `squill {first} {second}` is not a registered command")
        else:
            bad.append(f"{doc}: `squill {first}` is not a registered command")
    assert not bad, "docs invoke commands not in the registry:\n" + "\n".join(bad)


def test_guard_rejects_a_renamed_command():
    # `record` was renamed to the `session` family; neither it nor the plural
    # group shorthands `windows`/`displays` are real command tokens. This pins
    # the invariant the check above relies on.
    for phantom in ("record", "windows", "displays"):
        assert phantom not in _LEAF
        assert phantom not in _GROUPS
