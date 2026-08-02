# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Channel-aware, allowlisted macOS uninstall planning."""

from __future__ import annotations

import os
import plistlib
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from shotquill import uninstall

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="macOS uninstall uses POSIX links")

_APP_GENERATION = f"1:2:{'a' * 64}"
_HELPER_GENERATION = f"3:4:{'b' * 64}"
_SHOTQUILL_GENERATION = f"5:6:{'c' * 64}"
_SQUILL_GENERATION = f"7:8:{'d' * 64}"
_LAUNCH_AGENT_GENERATION = f"9:10:{'e' * 64}"


def _bind_plan(plan: uninstall.UninstallPlan) -> uninstall.UninstallPlan:
    removed = {path.name for path in plan.remove_paths}
    return replace(
        plan,
        generation=uninstall.InstallGeneration(
            app=_APP_GENERATION if "ShotQuill.app" in removed else "missing",
            helper=_HELPER_GENERATION,
            shotquill=_SHOTQUILL_GENERATION if "shotquill" in removed else "preserve",
            squill=_SQUILL_GENERATION if "squill" in removed else "preserve",
            launch_agent=_LAUNCH_AGENT_GENERATION,
        ),
    )


def _write_app(root: Path, *, bundle_id: str = uninstall.APP_BUNDLE_ID) -> Path:
    app = root / "Applications" / "ShotQuill.app"
    contents = app / "Contents"
    executable = contents / "MacOS" / "ShotQuill"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"executable")
    executable.chmod(0o755)
    with (contents / "Info.plist").open("wb") as file:
        plistlib.dump({"CFBundleIdentifier": bundle_id}, file)
    return app


def _write_helper(root: Path) -> Path:
    helper = root / uninstall.UNINSTALL_HELPER_PATH.removeprefix("/")
    helper.parent.mkdir(parents=True)
    (root / "Library").chmod(0o755)
    helper.parent.chmod(0o755)
    helper.write_text("#!/bin/bash\n", encoding="utf-8")
    helper.chmod(0o755)
    return helper


def _write_direct_links(root: Path, *, target: str = uninstall.APP_EXECUTABLE) -> tuple[Path, ...]:
    command_dir = root / "usr" / "local" / "bin"
    command_dir.mkdir(parents=True)
    links = tuple(command_dir / command for command in ("shotquill", "squill"))
    for link in links:
        link.symlink_to(target)
    return links


def _write_brew(root: Path) -> Path:
    brew = root / "opt" / "homebrew" / "bin" / "brew"
    brew.parent.mkdir(parents=True)
    brew.write_text("#!/bin/sh\n", encoding="utf-8")
    brew.chmod(0o755)
    return brew


def test_homebrew_registration_wins_over_the_shared_app_receipt(tmp_path):
    _write_app(tmp_path)
    _write_helper(tmp_path)
    brew = _write_brew(tmp_path)

    state = uninstall.inspect_macos_installation(
        root=tmp_path,
        receipt_exists=lambda receipt: receipt == uninstall.APP_RECEIPT,
        brew_probe=lambda candidate: (
            uninstall.BrewProbeResult.REGISTERED
            if candidate == brew
            else uninstall.BrewProbeResult.ABSENT
        ),
    )
    plan = uninstall.build_uninstall_plan(state)

    assert state.channel is uninstall.InstallChannel.HOMEBREW
    assert plan.brew_command == (str(brew), "uninstall", "--cask", "shotquill")
    assert plan.remove_paths == ()
    assert plan.forget_receipts == ()


def test_direct_pkg_plan_only_removes_owned_fixed_paths(tmp_path):
    app = _write_app(tmp_path)
    helper = _write_helper(tmp_path)
    links = _write_direct_links(tmp_path)

    state = uninstall.inspect_macos_installation(
        root=tmp_path,
        receipt_exists=lambda _receipt: True,
        brew_probe=lambda _candidate: uninstall.BrewProbeResult.ABSENT,
    )
    plan = uninstall.build_uninstall_plan(state)

    assert state.channel is uninstall.InstallChannel.PKG
    assert state.app_state is uninstall.ItemState.OWNED
    assert plan.can_execute is True
    assert set(plan.remove_paths) == {app, helper, *links}
    assert plan.forget_receipts == (
        uninstall.APP_RECEIPT,
        uninstall.CLI_RECEIPT,
        uninstall.UNINSTALL_RECEIPT,
    )
    assert plan.helper_path == helper


def test_unrelated_cli_command_is_preserved(tmp_path):
    app = _write_app(tmp_path)
    _write_helper(tmp_path)
    links = _write_direct_links(tmp_path)
    links[0].unlink()
    links[0].write_text("unrelated", encoding="utf-8")
    links[1].unlink()
    links[1].symlink_to("/another/ShotQuill")

    state = uninstall.inspect_macos_installation(
        root=tmp_path,
        receipt_exists=lambda _receipt: True,
        brew_probe=lambda _candidate: uninstall.BrewProbeResult.ABSENT,
    )
    plan = uninstall.build_uninstall_plan(state)

    assert plan.remove_paths == (app, tmp_path / uninstall.UNINSTALL_HELPER_PATH.removeprefix("/"))
    assert all(link.state is uninstall.ItemState.UNRELATED for link in state.cli_links)
    assert any("Keeping" in warning for warning in plan.warnings)


def test_bundle_id_mismatch_blocks_direct_uninstall(tmp_path):
    _write_app(tmp_path, bundle_id="com.example.not-shotquill")
    _write_helper(tmp_path)
    _write_direct_links(tmp_path)

    state = uninstall.inspect_macos_installation(
        root=tmp_path,
        receipt_exists=lambda _receipt: True,
        brew_probe=lambda _candidate: uninstall.BrewProbeResult.ABSENT,
    )
    plan = uninstall.build_uninstall_plan(state)

    assert state.app_state is uninstall.ItemState.UNRELATED
    assert plan.can_execute is False
    assert plan.remove_paths == ()
    assert any("bundle" in warning.casefold() for warning in plan.warnings)


def test_homebrew_cli_receipt_is_normal_pkg_component_state(tmp_path):
    _write_app(tmp_path)
    _write_helper(tmp_path)
    brew = _write_brew(tmp_path)

    state = uninstall.inspect_macos_installation(
        root=tmp_path,
        receipt_exists=lambda _receipt: True,
        brew_probe=lambda _candidate: uninstall.BrewProbeResult.REGISTERED,
    )
    plan = uninstall.build_uninstall_plan(state)

    assert state.channel is uninstall.InstallChannel.HOMEBREW
    assert state.cli_receipt is True
    assert plan.warnings == ()
    assert plan.brew_command == (str(brew), "uninstall", "--cask", "shotquill")


def test_unmanaged_app_is_never_automatically_removed(tmp_path):
    _write_app(tmp_path)
    _write_helper(tmp_path)

    state = uninstall.inspect_macos_installation(
        root=tmp_path,
        receipt_exists=lambda _receipt: False,
        brew_probe=lambda _candidate: uninstall.BrewProbeResult.ABSENT,
    )
    plan = uninstall.build_uninstall_plan(state)

    assert state.channel is uninstall.InstallChannel.UNMANAGED
    assert plan.can_execute is False
    assert plan.remove_paths == ()


def test_gui_coordinator_only_passes_clean_fixed_helper_command(tmp_path):
    _write_app(tmp_path)
    helper = _write_helper(tmp_path)
    _write_direct_links(tmp_path)
    state = uninstall.inspect_macos_installation(
        root=tmp_path,
        receipt_exists=lambda _receipt: True,
        brew_probe=lambda _candidate: uninstall.BrewProbeResult.ABSENT,
    )
    plan = _bind_plan(uninstall.build_uninstall_plan(state))

    argv = uninstall.gui_coordinator_argv(plan, parent_pid=4321, language="zh")

    assert argv[:2] == ("/usr/bin/env", "-i")
    assert str(helper) in argv
    assert argv[-9:] == (
        "--launch-gui-coordinator",
        "4321",
        "zh",
        _APP_GENERATION,
        _HELPER_GENERATION,
        _SHOTQUILL_GENERATION,
        _SQUILL_GENERATION,
        _LAUNCH_AGENT_GENERATION,
        "r111",
    )
    assert "/Applications/ShotQuill.app/Contents/Resources" not in argv
    assert "--noprofile" in argv
    assert "--norc" in argv
    assert "Pictures" not in argv
    assert str(Path.home()) not in argv


def test_direct_plan_binding_captures_exact_generations_before_consent(tmp_path):
    _write_app(tmp_path)
    helper = _write_helper(tmp_path)
    _write_direct_links(tmp_path)
    state = uninstall.inspect_macos_installation(
        root=tmp_path,
        receipt_exists=lambda _receipt: True,
        brew_probe=lambda _candidate: uninstall.BrewProbeResult.ABSENT,
    )
    plan = uninstall.build_uninstall_plan(state)
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=(
                f"v2 {_APP_GENERATION} {_HELPER_GENERATION} "
                f"{_SHOTQUILL_GENERATION} {_SQUILL_GENERATION} "
                f"{_LAUNCH_AGENT_GENERATION}\n"
            ),
            stderr="",
        )

    bound = uninstall.bind_direct_plan(plan, runner=runner)

    assert bound.generation == uninstall.InstallGeneration(
        _APP_GENERATION,
        _HELPER_GENERATION,
        _SHOTQUILL_GENERATION,
        _SQUILL_GENERATION,
        _LAUNCH_AGENT_GENERATION,
    )
    assert calls[0][0][-5:] == (
        str(helper),
        "--inspect-direct",
        "owned",
        "owned",
        "owned",
    )
    assert calls[0][1]["timeout"] == 30


def test_failed_binding_clears_actions_and_localizes_the_warning(tmp_path, monkeypatch):
    _write_app(tmp_path)
    _write_helper(tmp_path)
    _write_direct_links(tmp_path)
    state = uninstall.inspect_macos_installation(
        root=tmp_path,
        receipt_exists=lambda _receipt: True,
        brew_probe=lambda _candidate: uninstall.BrewProbeResult.ABSENT,
    )
    monkeypatch.setattr(uninstall, "inspect_macos_installation", lambda: state)
    monkeypatch.setattr(
        uninstall,
        "bind_direct_plan",
        lambda _plan: (_ for _ in ()).throw(OSError("changed")),
    )

    plan = uninstall.prepare_uninstall_plan()
    rendered = uninstall.format_uninstall_plan(plan, language="zh")

    assert plan.can_execute is False
    assert plan.remove_paths == ()
    assert plan.forget_receipts == ()
    assert "安装状态已变化或无法安全绑定" in rendered
    assert "移除 /Applications" not in rendered


def test_plan_explicitly_preserves_user_data_and_screenshots(tmp_path):
    _write_app(tmp_path)
    _write_helper(tmp_path)
    state = uninstall.inspect_macos_installation(
        root=tmp_path,
        receipt_exists=lambda _receipt: True,
        brew_probe=lambda _candidate: uninstall.BrewProbeResult.ABSENT,
    )

    rendered = uninstall.format_uninstall_plan(uninstall.build_uninstall_plan(state))

    assert "settings" in rendered
    assert "recordings" in rendered
    assert "screenshots" in rendered
    assert "close other running" in rendered
    assert "launch-at-login" in rendered
    assert os.fspath(tmp_path / "Pictures" / "ShotQuill") not in rendered


def test_plan_can_be_rendered_in_chinese(tmp_path):
    _write_app(tmp_path)
    _write_helper(tmp_path)
    state = uninstall.inspect_macos_installation(
        root=tmp_path,
        receipt_exists=lambda _receipt: True,
        brew_probe=lambda _candidate: uninstall.BrewProbeResult.ABSENT,
    )

    rendered = uninstall.format_uninstall_plan(
        uninstall.build_uninstall_plan(state),
        language="zh",
    )

    assert "安装来源：直接 PKG" in rendered
    assert "将执行：" in rendered
    assert "移除" in rendered
    assert "将保留：" in rendered
    assert "设置、排除名单和允许名单" in rendered


def test_direct_cli_execution_replaces_the_app_process_with_the_fixed_coordinator(tmp_path):
    _write_app(tmp_path)
    helper = _write_helper(tmp_path)
    state = uninstall.inspect_macos_installation(
        root=tmp_path,
        receipt_exists=lambda _receipt: True,
        brew_probe=lambda _candidate: uninstall.BrewProbeResult.ABSENT,
    )
    plan = _bind_plan(uninstall.build_uninstall_plan(state))
    calls = []

    def fake_execv(executable, argv):
        calls.append((executable, argv))
        raise RuntimeError("exec stops the current image")

    with pytest.raises(RuntimeError, match="exec stops"):
        uninstall.execute_cli_uninstall(plan, execv=fake_execv)

    expected = [
        "/usr/bin/env",
        "-i",
        "PATH=/usr/bin:/bin:/usr/sbin:/sbin",
        "/bin/bash",
        "--noprofile",
        "--norc",
        str(helper),
        "--cli-coordinator",
        "direct",
        _APP_GENERATION,
        _HELPER_GENERATION,
        "preserve",
        "preserve",
        _LAUNCH_AGENT_GENERATION,
        "r111",
    ]
    assert calls == [("/usr/bin/env", expected)]


def test_brew_cli_execution_replaces_the_process_without_sudo(tmp_path):
    _write_app(tmp_path)
    _write_helper(tmp_path)
    brew = _write_brew(tmp_path)
    state = uninstall.inspect_macos_installation(
        root=tmp_path,
        receipt_exists=lambda _receipt: True,
        brew_probe=lambda _candidate: uninstall.BrewProbeResult.REGISTERED,
    )
    plan = uninstall.build_uninstall_plan(state)
    calls = []

    def fake_execv(executable, argv):
        calls.append((executable, argv))
        raise RuntimeError("exec stops the current image")

    try:
        uninstall.execute_cli_uninstall(plan, execv=fake_execv)
    except RuntimeError as exc:
        assert str(exc) == "exec stops the current image"

    assert calls == [
        (str(brew), [str(brew), "uninstall", "--cask", "shotquill"]),
    ]


def test_brew_probe_failure_blocks_direct_pkg_fallback(tmp_path):
    _write_app(tmp_path)
    _write_helper(tmp_path)
    _write_brew(tmp_path)

    state = uninstall.inspect_macos_installation(
        root=tmp_path,
        receipt_exists=lambda _receipt: True,
        brew_probe=lambda _candidate: uninstall.BrewProbeResult.ERROR,
    )
    plan = uninstall.build_uninstall_plan(state)

    assert state.brew_probe_failed is True
    assert plan.can_execute is False
    assert any("Homebrew" in warning for warning in plan.warnings)


def test_homebrew_bundle_mismatch_blocks_automatic_uninstall(tmp_path):
    _write_app(tmp_path, bundle_id="com.example.not-shotquill")
    _write_helper(tmp_path)
    brew = _write_brew(tmp_path)

    state = uninstall.inspect_macos_installation(
        root=tmp_path,
        receipt_exists=lambda _receipt: True,
        brew_probe=lambda _candidate: uninstall.BrewProbeResult.REGISTERED,
    )
    plan = uninstall.build_uninstall_plan(state)

    assert state.brew_executable == brew
    assert state.app_state is uninstall.ItemState.UNRELATED
    assert plan.can_execute is False
    assert plan.brew_command is None


def test_missing_app_can_clean_up_installer_residue(tmp_path):
    helper = _write_helper(tmp_path)
    links = _write_direct_links(tmp_path)

    state = uninstall.inspect_macos_installation(
        root=tmp_path,
        receipt_exists=lambda _receipt: True,
        brew_probe=lambda _candidate: uninstall.BrewProbeResult.ABSENT,
    )
    plan = uninstall.build_uninstall_plan(state)

    assert state.app_state is uninstall.ItemState.MISSING
    assert plan.can_execute is True
    assert set(plan.remove_paths) == {helper, *links}
    assert any("already missing" in warning for warning in plan.warnings)


def test_stale_caskroom_without_brew_blocks_pkg_fallback(tmp_path):
    _write_app(tmp_path)
    _write_helper(tmp_path)
    (tmp_path / "opt" / "homebrew" / "Caskroom" / "shotquill").mkdir(parents=True)

    state = uninstall.inspect_macos_installation(
        root=tmp_path,
        receipt_exists=lambda _receipt: True,
        brew_probe=lambda _candidate: uninstall.BrewProbeResult.ABSENT,
    )
    plan = uninstall.build_uninstall_plan(state)

    assert state.brew_probe_failed is True
    assert plan.can_execute is False


def test_default_brew_probe_parses_the_complete_cask_list(monkeypatch):
    responses = iter(
        (
            SimpleNamespace(returncode=0, stdout="shotquill 0.1.0\nother 2.0\n"),
            SimpleNamespace(returncode=0, stdout="other 2.0\n"),
            SimpleNamespace(returncode=2, stdout=""),
        )
    )
    monkeypatch.setattr(uninstall.subprocess, "run", lambda *_args, **_kwargs: next(responses))

    assert uninstall._default_brew_probe(Path("/opt/homebrew/bin/brew")) is (
        uninstall.BrewProbeResult.REGISTERED
    )
    assert uninstall._default_brew_probe(Path("/opt/homebrew/bin/brew")) is (
        uninstall.BrewProbeResult.ABSENT
    )
    assert uninstall._default_brew_probe(Path("/opt/homebrew/bin/brew")) is (
        uninstall.BrewProbeResult.ERROR
    )


def test_unsafe_helper_parent_blocks_automatic_uninstall(tmp_path):
    _write_app(tmp_path)
    helper = _write_helper(tmp_path)
    helper.parent.chmod(0o775)

    state = uninstall.inspect_macos_installation(
        root=tmp_path,
        receipt_exists=lambda _receipt: True,
        brew_probe=lambda _candidate: uninstall.BrewProbeResult.ABSENT,
    )
    plan = uninstall.build_uninstall_plan(state)

    assert state.helper_ready is False
    assert plan.can_execute is False


def test_extended_acl_blocks_automatic_uninstall(tmp_path, monkeypatch):
    _write_app(tmp_path)
    helper = _write_helper(tmp_path)
    monkeypatch.setattr(
        uninstall,
        "_path_has_extended_acl",
        lambda path: path == helper,
    )

    state = uninstall.inspect_macos_installation(
        root=tmp_path,
        receipt_exists=lambda _receipt: True,
        brew_probe=lambda _candidate: uninstall.BrewProbeResult.ABSENT,
    )

    assert state.helper_ready is False
    assert uninstall.build_uninstall_plan(state).can_execute is False
