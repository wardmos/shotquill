# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Inspect and safely route ShotQuill uninstallation.

The App receipt identifies the installed product, not whether Homebrew or a
double-clicked PKG invoked Installer. Homebrew registration therefore takes
precedence when choosing the owner. Direct-PKG removal stays behind a fixed,
package-owned helper; this module never accepts deletion paths from callers.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import stat
import subprocess
import sys
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

APP_BUNDLE_ID = "com.wardmos.shotquill"
APP_RECEIPT = "com.wardmos.shotquill.app"
CLI_RECEIPT = "com.wardmos.shotquill.cli"
UNINSTALL_RECEIPT = "com.wardmos.shotquill.uninstaller"
APP_PATH = "/Applications/ShotQuill.app"
APP_EXECUTABLE = f"{APP_PATH}/Contents/MacOS/ShotQuill"
UNINSTALL_HELPER_NAME = "com.wardmos.shotquill.uninstall"
UNINSTALL_HELPER_PATH = f"/Library/PrivilegedHelperTools/{UNINSTALL_HELPER_NAME}"
_DIRECT_COMMANDS = ("shotquill", "squill")
_BREW_LOCATIONS = ("/opt/homebrew/bin/brew", "/usr/local/bin/brew")
_CLEAN_HELPER_PREFIX = (
    "/usr/bin/env",
    "-i",
    "PATH=/usr/bin:/bin:/usr/sbin:/sbin",
    "/bin/bash",
    "--noprofile",
    "--norc",
)
_PRESERVED = (
    "settings, blocklist, and allowlist",
    "audit logs and recordings",
    "screenshots and every custom save folder",
)


class InstallChannel(Enum):
    """The owner that must perform removal."""

    HOMEBREW = "homebrew"
    PKG = "pkg"
    UNMANAGED = "unmanaged"
    NOT_INSTALLED = "not-installed"


class ItemState(Enum):
    """Whether a fixed path is absent, ours, or must be preserved."""

    MISSING = "missing"
    OWNED = "owned"
    UNRELATED = "unrelated"


class BrewProbeResult(Enum):
    """Whether a working Homebrew owns the ShotQuill cask."""

    REGISTERED = "registered"
    ABSENT = "absent"
    ERROR = "error"


@dataclass(frozen=True)
class LinkInspection:
    path: Path
    state: ItemState
    target: str | None = None


@dataclass(frozen=True)
class MacInstallState:
    root: Path
    channel: InstallChannel
    brew_executable: Path | None
    app_path: Path
    app_state: ItemState
    helper_path: Path
    helper_ready: bool
    brew_probe_failed: bool
    app_receipt: bool
    cli_receipt: bool
    uninstall_receipt: bool
    cli_links: tuple[LinkInspection, ...]


@dataclass(frozen=True)
class UninstallPlan:
    channel: InstallChannel
    can_execute: bool
    remove_paths: tuple[Path, ...]
    forget_receipts: tuple[str, ...]
    brew_command: tuple[str, ...] | None
    helper_path: Path | None
    warnings: tuple[str, ...]
    preserved: tuple[str, ...] = _PRESERVED
    generation: InstallGeneration | None = None


@dataclass(frozen=True)
class InstallGeneration:
    """Exact fixed-path objects that were inspected before user consent."""

    app: str
    helper: str
    shotquill: str
    squill: str
    launch_agent: str


def _rooted(root: Path, absolute: str) -> Path:
    return root / absolute.removeprefix("/")


def _path_present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _inspect_app(app: Path) -> ItemState:
    if not _path_present(app):
        return ItemState.MISSING
    try:
        info = app.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            return ItemState.UNRELATED

        plist = app / "Contents" / "Info.plist"
        executable = app / "Contents" / "MacOS" / "ShotQuill"
        plist_info = plist.lstat()
        executable_info = executable.lstat()
        if not stat.S_ISREG(plist_info.st_mode) or stat.S_ISLNK(plist_info.st_mode):
            return ItemState.UNRELATED
        if not stat.S_ISREG(executable_info.st_mode) or stat.S_ISLNK(executable_info.st_mode):
            return ItemState.UNRELATED
        with plist.open("rb") as file:
            metadata = plistlib.load(file)
        if metadata.get("CFBundleIdentifier") != APP_BUNDLE_ID:
            return ItemState.UNRELATED
    except (OSError, ValueError, plistlib.InvalidFileException):
        return ItemState.UNRELATED
    return ItemState.OWNED


def _inspect_link(path: Path) -> LinkInspection:
    if not _path_present(path):
        return LinkInspection(path, ItemState.MISSING)
    try:
        info = path.lstat()
        if not stat.S_ISLNK(info.st_mode):
            return LinkInspection(path, ItemState.UNRELATED)
        target = os.readlink(path)
    except OSError:
        return LinkInspection(path, ItemState.UNRELATED)
    state = ItemState.OWNED if target == APP_EXECUTABLE else ItemState.UNRELATED
    return LinkInspection(path, state, target)


def _default_receipt_exists(receipt: str) -> bool:
    try:
        result = subprocess.run(
            ["/usr/sbin/pkgutil", "--pkg-info", receipt],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _default_brew_probe(brew: Path) -> BrewProbeResult:
    try:
        result = subprocess.run(
            [str(brew), "list", "--cask", "--versions"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return BrewProbeResult.ERROR
    if result.returncode != 0:
        return BrewProbeResult.ERROR
    registered = any(
        line.split(maxsplit=1)[0] == "shotquill"
        for line in result.stdout.splitlines()
        if line.split()
    )
    return BrewProbeResult.REGISTERED if registered else BrewProbeResult.ABSENT


def _find_registered_brew(
    root: Path,
    *,
    brew_probe: Callable[[Path], BrewProbeResult],
) -> tuple[Path | None, bool]:
    candidates = [_rooted(root, location) for location in _BREW_LOCATIONS]
    probe_failed = False
    for candidate in candidates:
        caskroom = candidate.parent.parent / "Caskroom" / "shotquill"
        caskroom_present = _path_present(caskroom)
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            probe_failed = probe_failed or caskroom_present
            continue
        result = brew_probe(candidate)
        if result is BrewProbeResult.REGISTERED:
            return candidate, False
        if result is BrewProbeResult.ERROR or caskroom_present:
            probe_failed = True
    return None, probe_failed


def _path_has_extended_acl(path: Path) -> bool:
    """Fail closed when macOS reports an ACL on a protected helper path."""
    if sys.platform != "darwin":
        return False
    try:
        result = subprocess.run(
            ["/bin/ls", "-lde", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
            env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return True
    return result.returncode != 0 or len(result.stdout.splitlines()) != 1


def _helper_is_ready(root: Path, helper: Path) -> bool:
    protected = (
        root,
        _rooted(root, "/Library"),
        _rooted(root, "/Library/PrivilegedHelperTools"),
    )
    try:
        helper_info = helper.lstat()
        expected_uid = 0 if root == Path("/") else helper_info.st_uid
        for directory in protected:
            info = directory.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or info.st_uid != expected_uid
                or stat.S_IMODE(info.st_mode) & 0o022
            ):
                return False
            if _path_has_extended_acl(directory):
                return False
        return (
            stat.S_ISREG(helper_info.st_mode)
            and not stat.S_ISLNK(helper_info.st_mode)
            and helper_info.st_uid == expected_uid
            and not stat.S_IMODE(helper_info.st_mode) & 0o022
            and os.access(helper, os.X_OK)
            and not _path_has_extended_acl(helper)
        )
    except OSError:
        return False


def inspect_macos_installation(
    *,
    root: Path = Path("/"),
    receipt_exists: Callable[[str], bool] = _default_receipt_exists,
    brew_probe: Callable[[Path], BrewProbeResult] = _default_brew_probe,
) -> MacInstallState:
    """Inspect fixed macOS paths without mutating them."""
    root = Path(root)
    app = _rooted(root, APP_PATH)
    helper = _rooted(root, UNINSTALL_HELPER_PATH)
    app_state = _inspect_app(app)
    helper_ready = _helper_is_ready(root, helper)
    links = tuple(
        _inspect_link(_rooted(root, f"/usr/local/bin/{command}")) for command in _DIRECT_COMMANDS
    )
    app_receipt = receipt_exists(APP_RECEIPT)
    cli_receipt = receipt_exists(CLI_RECEIPT)
    uninstall_receipt = receipt_exists(UNINSTALL_RECEIPT)
    brew, brew_probe_failed = _find_registered_brew(root, brew_probe=brew_probe)

    if brew is not None:
        channel = InstallChannel.HOMEBREW
    elif app_receipt or cli_receipt or uninstall_receipt:
        channel = InstallChannel.PKG
    elif app_state is ItemState.OWNED:
        channel = InstallChannel.UNMANAGED
    else:
        channel = InstallChannel.NOT_INSTALLED

    return MacInstallState(
        root=root,
        channel=channel,
        brew_executable=brew,
        app_path=app,
        app_state=app_state,
        helper_path=helper,
        helper_ready=helper_ready,
        brew_probe_failed=brew_probe_failed,
        app_receipt=app_receipt,
        cli_receipt=cli_receipt,
        uninstall_receipt=uninstall_receipt,
        cli_links=links,
    )


def build_uninstall_plan(state: MacInstallState) -> UninstallPlan:
    """Build an immutable deletion/delegation plan from an inspection."""
    warnings: list[str] = []
    if state.brew_probe_failed:
        warnings.append(
            "Homebrew ownership could not be determined safely; repair Homebrew and retry."
        )
        return UninstallPlan(
            channel=state.channel,
            can_execute=False,
            remove_paths=(),
            forget_receipts=(),
            brew_command=None,
            helper_path=None,
            warnings=tuple(warnings),
        )

    if state.channel is InstallChannel.HOMEBREW:
        if state.app_state is ItemState.UNRELATED:
            warnings.append(
                f"Refusing to remove {state.app_path}: it is not the expected ShotQuill bundle."
            )
            return UninstallPlan(
                channel=state.channel,
                can_execute=False,
                remove_paths=(),
                forget_receipts=(),
                brew_command=None,
                helper_path=None,
                warnings=tuple(warnings),
            )
        if not state.helper_ready:
            warnings.append(
                "The protected uninstall helper is missing or unsafe; repair ShotQuill first."
            )
            return UninstallPlan(
                channel=state.channel,
                can_execute=False,
                remove_paths=(),
                forget_receipts=(),
                brew_command=None,
                helper_path=None,
                warnings=tuple(warnings),
            )
        command = (
            str(state.brew_executable),
            "uninstall",
            "--cask",
            "shotquill",
        )
        return UninstallPlan(
            channel=state.channel,
            can_execute=True,
            remove_paths=(),
            forget_receipts=(),
            brew_command=command,
            helper_path=None,
            warnings=tuple(warnings),
        )

    if state.channel is InstallChannel.PKG:
        if state.app_state is ItemState.UNRELATED:
            warnings.append(
                f"Refusing to remove {state.app_path}: it is not the expected ShotQuill bundle."
            )
            return UninstallPlan(
                channel=state.channel,
                can_execute=False,
                remove_paths=(),
                forget_receipts=(),
                brew_command=None,
                helper_path=None,
                warnings=tuple(warnings),
            )
        for link in state.cli_links:
            if link.state is ItemState.UNRELATED:
                warnings.append(f"Keeping unrelated command at {link.path}.")
        if not state.helper_ready:
            warnings.append(
                "The protected uninstall helper is missing or unsafe; repair or reinstall "
                "ShotQuill first."
            )
        if state.app_state is ItemState.MISSING:
            warnings.append(
                "The application is already missing; only installer residue will be removed."
            )
        remove_paths = tuple(
            ([state.app_path] if state.app_state is ItemState.OWNED else [])
            + [link.path for link in state.cli_links if link.state is ItemState.OWNED]
            + [state.helper_path]
        )
        receipts = tuple(
            receipt
            for receipt, present in (
                (APP_RECEIPT, state.app_receipt),
                (CLI_RECEIPT, state.cli_receipt),
                (UNINSTALL_RECEIPT, state.uninstall_receipt),
            )
            if present
        )
        can_execute = state.app_state in {ItemState.OWNED, ItemState.MISSING} and state.helper_ready
        return UninstallPlan(
            channel=state.channel,
            can_execute=can_execute,
            remove_paths=remove_paths if can_execute else (),
            forget_receipts=receipts if can_execute else (),
            brew_command=None,
            helper_path=state.helper_path if can_execute else None,
            warnings=tuple(warnings),
        )

    if state.channel is InstallChannel.UNMANAGED:
        warnings.append("ShotQuill is not registered to Homebrew or the direct PKG; use Finder.")
    else:
        warnings.append("ShotQuill is not installed.")
    return UninstallPlan(
        channel=state.channel,
        can_execute=False,
        remove_paths=(),
        forget_receipts=(),
        brew_command=None,
        helper_path=None,
        warnings=tuple(warnings),
    )


def _warning_in_language(warning: str, language: str) -> str:
    if language != "zh":
        return warning
    exact = {
        "Homebrew ownership could not be determined safely; repair Homebrew and retry.": (
            "无法安全确认 Homebrew 的管理状态；请修复 Homebrew 后重试。"
        ),
        "The protected uninstall helper is missing or unsafe; repair ShotQuill first.": (
            "受保护的卸载辅助程序缺失或不安全；请先修复 ShotQuill。"
        ),
        "The protected uninstall helper is missing or unsafe; repair or reinstall ShotQuill "
        "first.": ("受保护的卸载辅助程序缺失或不安全；请先修复或重新安装 ShotQuill。"),
        "The application is already missing; only installer residue will be removed.": (
            "应用已经缺失；将只清理安装器残留。"
        ),
        "ShotQuill is not registered to Homebrew or the direct PKG; use Finder.": (
            "ShotQuill 未登记到 Homebrew 或直接 PKG；请使用访达移除。"
        ),
        "ShotQuill is not installed.": "未安装 ShotQuill。",
        "The installation changed or could not be bound safely; inspect it again.": (
            "安装状态已变化或无法安全绑定；请重新检查后再试。"
        ),
    }
    if warning in exact:
        return exact[warning]
    if warning.startswith("Refusing to remove "):
        return warning.replace("Refusing to remove ", "拒绝移除 ", 1).replace(
            ": it is not the expected ShotQuill bundle.",
            "：它不是预期的 ShotQuill 应用包。",
        )
    if warning.startswith("Keeping unrelated command at "):
        return warning.replace("Keeping unrelated command at ", "保留不相关的命令 ", 1)
    return warning


def format_uninstall_plan(plan: UninstallPlan, *, language: str = "en") -> str:
    """Render a stable human-readable preview shared by CLI and GUI."""
    if language not in {"en", "zh"}:
        raise ValueError("unsupported uninstall preview language")
    if language == "zh":
        channels = {
            InstallChannel.HOMEBREW: "Homebrew",
            InstallChannel.PKG: "直接 PKG",
            InstallChannel.UNMANAGED: "未受管理",
            InstallChannel.NOT_INSTALLED: "未安装",
        }
        lines = [f"安装来源：{channels[plan.channel]}", "将执行："]
    else:
        lines = [f"Install channel: {plan.channel.value}", "Actions:"]
    if plan.brew_command:
        lines.append("  " + shlex.join(plan.brew_command))
    for path in plan.remove_paths:
        action = "移除" if language == "zh" else "remove"
        lines.append(f"  {action} {path}")
    for receipt in plan.forget_receipts:
        action = "忘记软件包收据" if language == "zh" else "forget receipt"
        lines.append(f"  {action} {receipt}")
    if plan.can_execute:
        if language == "zh":
            lines.append("  关闭其他正在运行的 ShotQuill 进程")
            lines.append("  移除 ShotQuill 登录启动项（若存在）")
        else:
            lines.append("  close other running ShotQuill processes")
            lines.append("  remove ShotQuill launch-at-login entry if present")
    if (
        not plan.brew_command
        and not plan.remove_paths
        and not plan.forget_receipts
        and not plan.can_execute
    ):
        lines.append("  无" if language == "zh" else "  none")
    if plan.warnings:
        lines.append("警告：" if language == "zh" else "Warnings:")
        lines.extend(f"  {_warning_in_language(warning, language)}" for warning in plan.warnings)
    lines.append("将保留：" if language == "zh" else "Preserved:")
    if language == "zh":
        preserved = (
            "设置、排除名单和允许名单",
            "审计日志和录制",
            "截图及所有自定义保存目录",
        )
    else:
        preserved = plan.preserved
    lines.extend(f"  {item}" for item in preserved)
    return "\n".join(lines)


def _helper_command(plan: UninstallPlan) -> tuple[str, ...]:
    if plan.channel is not InstallChannel.PKG or not plan.can_execute or plan.helper_path is None:
        raise ValueError("direct-PKG uninstall is not executable")
    return (*_CLEAN_HELPER_PREFIX, str(plan.helper_path))


def _direct_scope(plan: UninstallPlan) -> tuple[str, str, str, str]:
    """Bind execution to the paths and receipts shown in the confirmed preview."""
    removed_names = {path.name for path in plan.remove_paths}
    app_policy = "owned" if Path(APP_PATH).name in removed_names else "missing"
    shotquill_policy = "owned" if "shotquill" in removed_names else "preserve"
    squill_policy = "owned" if "squill" in removed_names else "preserve"
    receipt_mask = "r" + "".join(
        "1" if receipt in plan.forget_receipts else "0"
        for receipt in (APP_RECEIPT, CLI_RECEIPT, UNINSTALL_RECEIPT)
    )
    return app_policy, shotquill_policy, squill_policy, receipt_mask


def _valid_generation_token(token: str) -> bool:
    if token in {"missing", "preserve"}:
        return True
    parts = token.split(":")
    return (
        len(parts) == 3
        and all(part.isdecimal() for part in parts[:2])
        and len(parts[2]) == 64
        and all(character in "0123456789abcdef" for character in parts[2])
    )


def bind_direct_plan(
    plan: UninstallPlan,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> UninstallPlan:
    """Capture exact fixed-path generations before presenting destructive consent."""
    app_policy, shotquill_policy, squill_policy, _receipt_mask = _direct_scope(plan)
    command = (
        *_helper_command(plan),
        "--inspect-direct",
        app_policy,
        shotquill_policy,
        squill_policy,
    )
    result = runner(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    fields = result.stdout.strip().split()
    if result.returncode != 0 or result.stderr or len(fields) != 6 or fields[0] != "v2":
        raise OSError("the protected helper could not bind the uninstall preview")
    tokens = fields[1:]
    if not all(_valid_generation_token(token) for token in tokens):
        raise OSError("the protected helper returned an invalid install generation")
    generation = InstallGeneration(*tokens)
    if app_policy == "owned" and generation.app in {"missing", "preserve"}:
        raise OSError("the ShotQuill app changed during inspection")
    if app_policy == "missing" and generation.app != "missing":
        raise OSError("a ShotQuill app appeared during inspection")
    if generation.helper in {"missing", "preserve"}:
        raise OSError("the protected helper changed during inspection")
    for policy, token in (
        (shotquill_policy, generation.shotquill),
        (squill_policy, generation.squill),
    ):
        if policy == "owned" and token in {"missing", "preserve"}:
            raise OSError("a ShotQuill command changed during inspection")
        if policy == "preserve" and token != "preserve":
            raise OSError("the protected helper exceeded the uninstall preview")
    return replace(plan, generation=generation)


def prepare_uninstall_plan() -> UninstallPlan:
    """Inspect channel/scope and bind direct-PKG consent to exact generations."""
    plan = build_uninstall_plan(inspect_macos_installation())
    if plan.channel is InstallChannel.PKG and plan.can_execute and plan.generation is None:
        try:
            return bind_direct_plan(plan)
        except (OSError, subprocess.SubprocessError):
            return replace(
                plan,
                can_execute=False,
                remove_paths=(),
                forget_receipts=(),
                helper_path=None,
                generation=None,
                warnings=plan.warnings
                + ("The installation changed or could not be bound safely; inspect it again.",),
            )
    return plan


def gui_coordinator_argv(
    plan: UninstallPlan,
    *,
    parent_pid: int | None = None,
    language: str,
) -> tuple[str, ...]:
    """Return a fixed launcher for the post-exit user coordinator."""
    if parent_pid is None:
        parent_pid = os.getpid()
    if isinstance(parent_pid, bool) or not isinstance(parent_pid, int) or parent_pid <= 0:
        raise ValueError("uninstall parent PID must be a positive integer")
    if language not in {"en", "zh"}:
        raise ValueError("unsupported uninstall UI language")
    if plan.generation is None:
        raise ValueError("direct-PKG uninstall plan is not bound to user consent")
    _app_policy, _shotquill_policy, _squill_policy, receipt_mask = _direct_scope(plan)
    return (
        *_helper_command(plan),
        "--launch-gui-coordinator",
        str(parent_pid),
        language,
        plan.generation.app,
        plan.generation.helper,
        plan.generation.shotquill,
        plan.generation.squill,
        plan.generation.launch_agent,
        receipt_mask,
    )


def execute_cli_uninstall(
    plan: UninstallPlan,
    *,
    execv: Callable[[str, list[str]], object] = os.execv,
) -> int:
    """Replace the app-backed CLI with a system-shell uninstall coordinator."""
    if not plan.can_execute:
        raise ValueError("uninstall plan is not executable")
    if plan.channel is InstallChannel.HOMEBREW:
        if not plan.brew_command:
            raise ValueError("Homebrew plan has no command")
        executable = plan.brew_command[0]
        execv(executable, list(plan.brew_command))
        return 1  # pragma: no cover - successful execv never returns
    if plan.channel is not InstallChannel.PKG or plan.helper_path is None:
        raise ValueError("unsupported uninstall channel")
    if plan.generation is None:
        raise ValueError("direct-PKG uninstall plan is not bound to user consent")
    _app_policy, _shotquill_policy, _squill_policy, receipt_mask = _direct_scope(plan)
    command = (
        *_helper_command(plan),
        "--cli-coordinator",
        "direct",
        plan.generation.app,
        plan.generation.helper,
        plan.generation.shotquill,
        plan.generation.squill,
        plan.generation.launch_agent,
        receipt_mask,
    )
    execv(command[0], list(command))
    return 1  # pragma: no cover - successful execv never returns
