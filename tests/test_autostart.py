# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Pure-logic tests for the macOS LaunchAgent plist (no filesystem, no .app)."""

from pathlib import Path

from shotquill.autostart.macos import (
    LAUNCH_AGENT_LABEL,
    MacAutostartManager,
    build_launch_agent_plist,
    launch_arguments,
)


def test_plist_contains_label_and_arguments():
    plist = build_launch_agent_plist("com.example.app", ["/path/to/bin", "-m", "pkg"])
    assert "<key>Label</key>" in plist
    assert "<string>com.example.app</string>" in plist
    assert "<string>/path/to/bin</string>" in plist
    assert "<string>-m</string>" in plist
    assert "<string>pkg</string>" in plist


def test_plist_runs_at_load_in_aqua_session():
    plist = build_launch_agent_plist(LAUNCH_AGENT_LABEL, ["/bin/true"])
    assert "<key>RunAtLoad</key>" in plist
    assert "<true/>" in plist
    assert "<string>Aqua</string>" in plist


def test_plist_escapes_special_characters():
    plist = build_launch_agent_plist("a&b", ["/Apps/My <App>/bin"])
    assert "a&amp;b" in plist
    assert "/Apps/My &lt;App&gt;/bin" in plist
    assert "<App>" not in plist


def test_launch_arguments_in_dev_uses_module_entrypoint():
    # Not frozen under pytest, so we expect `python -m shotquill`.
    args = launch_arguments()
    assert args[-2:] == ["-m", "shotquill"]
    assert args[0]  # interpreter path is non-empty


def test_enable_disable_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    manager = MacAutostartManager()

    assert manager.is_enabled() is False
    manager.enable()
    assert manager.is_enabled() is True
    plist = tmp_path / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"
    assert plist.exists()
    assert "<key>RunAtLoad</key>" in plist.read_text()

    manager.enable()  # idempotent
    assert manager.is_enabled() is True

    manager.disable()
    assert manager.is_enabled() is False
    manager.disable()  # idempotent, no error when already gone
