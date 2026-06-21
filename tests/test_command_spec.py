# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""The command registry is the single source for the CLI and MCP surfaces, so
these guard that the two handler maps stay in lockstep with it — and that the
generators fail loudly (not silently) when they don't."""

from __future__ import annotations

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
