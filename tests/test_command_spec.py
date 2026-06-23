# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""The command registry is the single source for the CLI and MCP surfaces, so
these guard that the two handler maps stay in lockstep with it — and that the
generators fail loudly (not silently) when they don't."""

from __future__ import annotations

import argparse

import pytest

from shotquill import cli, command_spec, mcp


def test_cli_handler_map_covers_the_registry_exactly():
    # Every command needs a handler, and no handler key may be orphaned — a typo
    # in either direction is a runtime break with no other static check.
    assert set(cli._handlers()) == {c.handler for c in command_spec.REGISTRY}


def test_mcp_handlers_and_output_schemas_cover_the_mcp_commands_exactly():
    mcp_names = {c.mcp_name for c in command_spec.mcp_commands()}
    assert set(mcp._HANDLERS) == mcp_names
    # Every MCP tool must ship an outputSchema (the typed-structuredContent
    # contract); a missing/orphan key would otherwise drop one silently.
    assert set(mcp.OUTPUT_SCHEMAS) == mcp_names


def test_build_argparse_rejects_an_orphan_handler():
    handlers = cli._handlers()
    handlers["ghost_command"] = lambda args: 0
    with pytest.raises(RuntimeError, match="orphan"):
        command_spec.build_argparse("0.0.0", handlers)


def test_build_mcp_tools_rejects_a_missing_output_schema():
    schemas = dict(mcp.OUTPUT_SCHEMAS)
    schemas.pop("diff")
    with pytest.raises(RuntimeError, match="OUTPUT_SCHEMAS"):
        command_spec.build_mcp_tools(mcp._HANDLERS, schemas)


def test_commands_exposes_the_registry_and_mcp_is_a_subset():
    cmds = command_spec.commands()
    assert cmds == command_spec.REGISTRY
    assert cmds  # the surface is never empty
    mcp_cmds = command_spec.mcp_commands()
    assert all(c in cmds for c in mcp_cmds)
    assert all(c.mcp_name is not None for c in mcp_cmds)


# --- argparse generation for param shapes the live registry doesn't exercise -


def test_mcp_only_param_is_omitted_from_the_cli_tree():
    # An mcp_only param is surface-exclusive; _add_cli_params must skip it so it
    # never appears as a CLI flag.
    cmd = command_spec.Command(
        cli_path=("x",),
        summary="synthetic",
        params=(command_spec.Param(name="secret", help="mcp-only", mcp_only=True),),
        handler="x",
    )
    parser = argparse.ArgumentParser()
    command_spec._add_cli_params(parser, cmd)
    assert "secret" not in {a.dest for a in parser._actions}


def test_typed_positional_params_parse_and_label():
    # int/float positionals get an argparse type=, and a metavar overrides the
    # usage label — none of which the live registry happens to combine.
    parser = argparse.ArgumentParser()
    command_spec._add_cli_one(
        parser,
        command_spec.Param(name="count", help="n", kind="int", positional=True),
        in_group=False,
    )
    command_spec._add_cli_one(
        parser,
        command_spec.Param(name="ratio", help="r", kind="float", positional=True),
        in_group=False,
    )
    command_spec._add_cli_one(
        parser,
        command_spec.Param(name="path", help="p", positional=True, metavar="FILE"),
        in_group=False,
    )
    ns = parser.parse_args(["5", "1.5", "out.png"])
    assert ns.count == 5 and isinstance(ns.count, int)
    assert ns.ratio == 1.5 and isinstance(ns.ratio, float)
    assert ns.path == "out.png"
    assert "FILE" in parser.format_usage()
