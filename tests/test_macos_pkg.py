# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Regression tests for the macOS product package definition."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import plistlib
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

import pytest

from shotquill.autostart.macos import LAUNCH_AGENT_LABEL, build_launch_agent_plist

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DISTRIBUTION_MODULE = _REPO_ROOT / "packaging" / "macos" / "pkg_distribution.py"
_BUILD_SCRIPT = _REPO_ROOT / "packaging" / "macos" / "build_pkg.sh"
_COMPONENTS_PLIST = _REPO_ROOT / "packaging" / "macos" / "app_components.plist"
_CLI_PREINSTALL = _REPO_ROOT / "packaging" / "macos" / "scripts" / "cli" / "preinstall"
_CLI_LINK_INSTALLER = _REPO_ROOT / "packaging" / "macos" / "cli_link_installer.c"
_UNINSTALL_HELPER = _REPO_ROOT / "packaging" / "macos" / "uninstall_pkg"
_TAP_SCRIPT = _REPO_ROOT / "packaging" / "macos" / "update_tap.sh"
_PKG_SMOKE_SCRIPT = _REPO_ROOT / "packaging" / "macos" / "smoke_pkg.sh"
_CASK_SMOKE_SCRIPT = _REPO_ROOT / "packaging" / "macos" / "smoke_cask.sh"
_PACKAGE_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "package.yml"
_RELEASE_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "release.yml"


def _load_distribution_module():
    spec = importlib.util.spec_from_file_location(
        "shotquill_pkg_distribution",
        _DISTRIBUTION_MODULE,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _choices(root):
    return {choice.attrib["id"]: choice for choice in root.findall("choice")}


def _package_refs(root):
    return {pkg.attrib["id"]: pkg for pkg in root.findall("pkg-ref")}


def test_distribution_requires_app_and_offers_cli_selected_by_default():
    module = _load_distribution_module()
    xml = module.render_distribution(
        version="0.1.0",
        app_package="ShotQuill-app.pkg",
        cli_package="ShotQuill-cli.pkg",
        uninstaller_package="ShotQuill-uninstaller.pkg",
    )
    root = ElementTree.fromstring(xml)

    assert root.tag == "installer-gui-script"
    assert root.attrib["minSpecVersion"] == "2"
    assert root.find("options").attrib == {
        "customize": "always",
        "require-scripts": "false",
    }
    assert root.find("domains").attrib == {
        "enable_anywhere": "false",
        "enable_currentUserHome": "false",
        "enable_localSystem": "true",
    }

    choices = _choices(root)
    assert choices["choice.app"].attrib["start_selected"] == "true"
    assert choices["choice.app"].attrib["start_enabled"] == "false"
    assert choices["choice.app"].attrib["start_visible"] == "false"
    assert choices["choice.cli"].attrib["start_selected"] == "true"
    assert choices["choice.cli"].attrib["start_enabled"] == "true"
    assert choices["choice.cli"].attrib["start_visible"] == "true"
    assert "/usr/local/bin" in choices["choice.cli"].attrib["description"]

    outline = root.find("choices-outline")
    assert [line.attrib["choice"] for line in outline.findall("line")] == [
        "choice.app",
        "choice.cli",
    ]


def test_distribution_separates_app_and_cli_packages():
    module = _load_distribution_module()
    root = ElementTree.fromstring(
        module.render_distribution(
            version="0.1.0",
            app_package="ShotQuill-app.pkg",
            cli_package="ShotQuill-cli.pkg",
            uninstaller_package="ShotQuill-uninstaller.pkg",
        )
    )

    choices = _choices(root)
    assert [ref.attrib["id"] for ref in choices["choice.app"].findall("pkg-ref")] == [
        "com.wardmos.shotquill.app",
        "com.wardmos.shotquill.uninstaller",
    ]
    assert choices["choice.cli"].find("pkg-ref").attrib["id"] == "com.wardmos.shotquill.cli"

    packages = _package_refs(root)
    assert packages["com.wardmos.shotquill.app"].attrib["version"] == "0.1.0"
    assert packages["com.wardmos.shotquill.app"].text == "ShotQuill-app.pkg"
    assert packages["com.wardmos.shotquill.cli"].attrib["version"] == "0.1.0"
    assert packages["com.wardmos.shotquill.cli"].text == "ShotQuill-cli.pkg"
    assert packages["com.wardmos.shotquill.uninstaller"].attrib["version"] == "0.1.0"
    assert packages["com.wardmos.shotquill.uninstaller"].text == "ShotQuill-uninstaller.pkg"
    assert all("auth" not in package.attrib for package in packages.values())


@pytest.mark.parametrize(
    ("version", "package"),
    [
        ("", "ShotQuill-app.pkg"),
        ("../0.1.0", "ShotQuill-app.pkg"),
        ("0.1.0", "../ShotQuill-app.pkg"),
        ("0.1.0", "/tmp/ShotQuill-app.pkg"),
    ],
)
def test_distribution_rejects_unsafe_values(version, package):
    module = _load_distribution_module()
    with pytest.raises(ValueError):
        module.render_distribution(
            version=version,
            app_package=package,
            cli_package="ShotQuill-cli.pkg",
            uninstaller_package="ShotQuill-uninstaller.pkg",
        )


def test_pkg_builder_replaces_the_dmg_container():
    script = _BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "pkgbuild" in script
    assert "productbuild" in script
    assert "pkg_distribution.py" in script
    assert "ShotQuill-$VERSION-$arch.pkg" in script
    assert "hdiutil" not in script
    assert "SHOTQUILL_DMG_FORMAT" not in script
    assert 'installer -showChoicesXML -pkg "$pkg" -target /' in script
    assert "install -m 0755 packaging/macos/uninstall_pkg" in script
    assert '"$uninstaller_root/com.wardmos.shotquill.uninstall"' in script
    assert "--install-location /Applications" in script
    assert "--install-location /Library/PrivilegedHelperTools" in script
    assert "--nopayload" in script
    assert '--scripts "$cli_scripts"' in script
    assert "xcrun clang" in script
    assert "cli_link_installer.c" in script
    assert "cli_arch_flags=(-arch arm64 -arch x86_64)" in script
    assert 'MACOS_MIN_VERSION="13.0"' in script
    assert '-mmacosx-version-min="$MACOS_MIN_VERSION"' in script
    assert "LSMinimumSystemVersion" in script
    assert 'codesign --force --sign - "$cli_scripts/postinstall"' in script


@pytest.mark.skipif(sys.platform == "win32", reason="macOS build script requires Bash")
@pytest.mark.parametrize(
    ("version", "arch", "message"),
    [
        ("../0.1.0", "arm64", "unsafe package version"),
        ("0.1.0", "../../outside", "unsupported architecture"),
    ],
)
def test_pkg_builder_rejects_unsafe_inputs_before_building(version, arch, message):
    result = subprocess.run(
        ["bash", str(_BUILD_SCRIPT), version, arch],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert message in result.stderr


def test_app_component_is_fixed_to_applications():
    with _COMPONENTS_PLIST.open("rb") as file:
        components = plistlib.load(file)

    assert components == [
        {
            "BundleHasStrictIdentifier": True,
            "BundleIsRelocatable": False,
            "BundleIsVersionChecked": True,
            "BundleOverwriteAction": "upgrade",
            "RootRelativeBundlePath": "ShotQuill.app",
        }
    ]


@pytest.mark.skipif(sys.platform == "win32", reason="macOS package script requires POSIX links")
def test_cli_preinstall_refuses_to_replace_an_unrelated_command(tmp_path):
    command_dir = tmp_path / "usr" / "local" / "bin"
    command_dir.mkdir(parents=True)
    (command_dir / "shotquill").write_text("unrelated", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(_CLI_PREINSTALL), "component.pkg", "/usr/local/bin", str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "refusing to replace" in result.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="macOS package script requires POSIX links")
def test_cli_preinstall_accepts_an_existing_shotquill_link(tmp_path):
    command_dir = tmp_path / "usr" / "local" / "bin"
    command_dir.mkdir(parents=True)
    target = "/Applications/ShotQuill.app/Contents/MacOS/ShotQuill"
    (command_dir / "shotquill").symlink_to(target)
    (command_dir / "squill").symlink_to(target)

    result = subprocess.run(
        ["bash", str(_CLI_PREINSTALL), "component.pkg", "/usr/local/bin", str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0


@pytest.mark.skipif(sys.platform == "win32", reason="macOS package script requires POSIX links")
def test_cli_preinstall_rejects_a_target_with_a_trailing_newline(tmp_path):
    command_dir = tmp_path / "usr" / "local" / "bin"
    command_dir.mkdir(parents=True)
    target = "/Applications/ShotQuill.app/Contents/MacOS/ShotQuill\n"
    (command_dir / "shotquill").symlink_to(target)

    result = subprocess.run(
        ["bash", str(_CLI_PREINSTALL), "component.pkg", "/usr/local/bin", str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "refusing to replace" in result.stderr


def test_cli_postinstall_uses_atomic_no_replace_and_generation_checked_rollback():
    source = _CLI_LINK_INSTALLER.read_text(encoding="utf-8")

    assert "renameatx_np" in source
    assert "RENAME_EXCL" in source
    assert "rename_exclusive" in source
    assert "AT_SYMLINK_NOFOLLOW" in source
    assert "identity_matches_at" in source
    assert "rollback_created_entries" in source
    assert "verify_directory_chain" in source
    assert '"/private/tmp"' in source
    assert "verify_sticky_temp_directory" in source
    assert "verify_same_filesystem" in source
    assert "acl_get_fd_np" in source
    assert "acl_set_fd_np" in source
    assert "acl_delete_fd_np" not in source
    assert "fchown(fd, 0" in source
    assert "fchmod(fd, 0700)" in source
    assert "verify_directory_empty" in source
    assert "umask(0022)" in source
    assert "unlinkat(bin_fd, entry->name" not in source


@pytest.mark.skipif(sys.platform == "win32", reason="macOS package script requires Bash")
def test_cli_package_scripts_have_valid_bash_syntax():
    result = subprocess.run(
        ["bash", "-n", str(_CLI_PREINSTALL)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"{_CLI_PREINSTALL}: {result.stderr}"
    assert "readlink -n" in _CLI_PREINSTALL.read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform == "win32", reason="macOS release scripts require Bash")
@pytest.mark.parametrize(
    "script",
    (_TAP_SCRIPT, _PKG_SMOKE_SCRIPT, _CASK_SMOKE_SCRIPT),
)
def test_macos_release_scripts_have_valid_bash_syntax(script):
    result = subprocess.run(
        ["bash", "-n", str(script)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"{script}: {result.stderr}"


@pytest.mark.skipif(sys.platform == "win32", reason="macOS release scripts require Bash")
def test_tap_script_can_render_the_exact_cask_without_publishing(tmp_path):
    output = tmp_path / "Casks" / "shotquill.rb"
    result = subprocess.run(
        ["bash", str(_TAP_SCRIPT), "v0.1.0", "a" * 64, "b" * 64],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "SHOTQUILL_CASK_OUTPUT": str(output), "TAP_TOKEN": ""},
    )

    assert result.returncode == 0, result.stderr
    cask = output.read_text(encoding="utf-8")
    assert 'version "0.1.0"' in cask
    assert f'sha256 arm:   "{"a" * 64}"' in cask
    assert f'intel: "{"b" * 64}"' in cask
    assert 'depends_on macos: ">= :ventura"' in cask
    assert '"attributeSetting" => 1' in cask
    assert '"$helper" --cli-coordinator' in cask


@pytest.mark.skipif(sys.platform == "win32", reason="macOS release scripts require Bash")
@pytest.mark.parametrize(
    ("version", "arm_sha", "intel_sha"),
    (
        ('0.1.0"\nend', "a" * 64, "b" * 64),
        ("0.1.0", "not-a-digest", "b" * 64),
        ("0.1.0", "a" * 64, "B" * 64),
    ),
)
def test_tap_script_rejects_unsafe_release_metadata(version, arm_sha, intel_sha, tmp_path):
    output = tmp_path / "shotquill.rb"
    result = subprocess.run(
        ["bash", str(_TAP_SCRIPT), version, arm_sha, intel_sha],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "SHOTQUILL_CASK_OUTPUT": str(output)},
    )

    assert result.returncode == 2
    assert not output.exists()


def test_package_and_release_workflows_share_macos_smoke_scripts():
    for workflow in (_PACKAGE_WORKFLOW, _RELEASE_WORKFLOW):
        source = workflow.read_text(encoding="utf-8")
        assert "packaging/macos/smoke_pkg.sh" in source
        assert "packaging/macos/smoke_cask.sh" in source


def test_pkg_smoke_refuses_existing_installs_and_removes_only_tracked_links():
    source = _PKG_SMOKE_SCRIPT.read_text(encoding="utf-8")

    assert "command failed at line $LINENO: $BASH_COMMAND" in source
    assert 'vtool -arch "$helper_arch" -show-build "$CLI_POSTINSTALL"' in source
    assert source.count("assert_no_existing_install") == 3
    assert "/Applications/ShotQuill.app" in source
    assert "/Library/PrivilegedHelperTools/com.wardmos.shotquill.uninstall" in source
    assert "/opt/homebrew/Caskroom/shotquill" in source
    assert "/usr/local/Caskroom/shotquill" in source
    assert '"$USER_HOME/Library/LaunchAgents/com.wardmos.shotquill.plist"' in source
    assert 'pkgutil --pkg-info "$receipt"' in source
    assert "SHOTQUILL_PROBE_IDENTITY" in source
    assert "SQUILL_PROBE_IDENTITY" in source
    assert "CLI probe is not a symbolic link" in source
    assert "cannot read CLI probe identity" in source
    assert "root_path_exists" in source
    assert "root_link_exists" in source
    assert '/usr/bin/sudo /bin/test -L "$1"' in source
    assert '/usr/bin/sudo /usr/bin/readlink -n "$path"' in source
    assert "root_link_target_matches" in source
    assert 'identity="$(/usr/bin/sudo /usr/bin/stat -f \'%d:%i\' "$path")"' in source
    assert "-showChoiceChangesXML" in source
    assert "assert_default_cli_selected" in source
    assert '-applyChoiceChangesXML "$DEFAULT_CHOICES"' in source
    assert "ROOT_STAGE_ACL" in source
    assert "stat -f '%d:%i' \"$staged\"" in source
    assert '/bin/mv -n "$path" "$staged"' in source
    assert '/bin/rm "$path"' not in source


def test_homebrew_cask_uses_the_guarded_pkg_cli_component():
    script = _TAP_SCRIPT.read_text(encoding="utf-8")

    assert "ShotQuill-#{version}-#{arch}.pkg" in script
    assert 'pkg "ShotQuill-#{version}-#{arch}.pkg"' in script
    assert "allow_untrusted: true" in script
    assert 'depends_on macos: ">= :ventura"' in script
    assert "SHOTQUILL_CASK_OUTPUT" in script
    assert '"choiceIdentifier" => "choice.cli"' in script
    assert '"attributeSetting" => 1' in script
    assert '"/Library/PrivilegedHelperTools/com.wardmos.shotquill.uninstall"' in script
    assert 'launchctl: "com.wardmos.shotquill"' not in script
    assert 'uninstall quit:   "com.wardmos.shotquill"' in script
    assert 'executable: "/usr/bin/env"' in script
    assert '"$helper" --cli-coordinator' in script
    assert "/bin/ls -lde" in script
    assert '"-i"' in script
    assert "pkgutil:" not in script
    assert "#{appdir}" not in script
    assert 'binary "/Applications/ShotQuill.app/Contents/MacOS/ShotQuill"' not in script
    assert 'system_command "/usr/bin/xattr"' not in script
    assert ".dmg" not in script


def test_pkg_uninstall_helper_revalidates_every_privileged_target():
    script = _UNINSTALL_HELPER.read_text(encoding="utf-8")

    assert "/Applications/ShotQuill.app" in script
    assert "com.wardmos.shotquill" in script
    assert "/Library/PrivilegedHelperTools/com.wardmos.shotquill.uninstall" in script
    assert "/usr/local/bin/shotquill" in script
    assert "/usr/local/bin/squill" in script
    assert "readlink -n" in script
    assert "readlink_status=$?" in script
    assert "shasum -a 256" in script
    assert "codesign --verify --deep --strict" in script
    assert "PlistBuddy" in script
    assert "pkgutil --forget" in script
    assert "pkgutil --files" not in script
    assert "mktemp -d" in script
    assert "--launch-gui-coordinator" in script
    assert "--gui-coordinator" in script
    assert "--cli-coordinator" in script
    assert "--root-uninstall" in script
    assert "require_user_handshake_directory" in script
    assert "require_no_extended_acl" in script
    assert "coordinator handshake already exists" in script
    assert "terminate_running_app_processes" in script
    assert "/usr/sbin/lsof" in script
    assert "-d txt" in script
    assert '[ ! -s "$error_file" ] || return 2' in script
    assert "pgrep" not in script
    assert "validate_bound_payload" in script
    assert "reopen_app_if_present" in script
    assert "show_gui_success" in script
    assert "cleanup_current_user_payload" in script
    assert "launchctl bootout" not in script
    assert "--worker" not in script
    assert "link_matches" in script
    assert "work_device" in script
    assert "cli_device" in script
    assert "/bin/mv -n" in script
    assert ".ShotQuill.uninstall.$$" not in script
    assert "Pictures" not in script
    assert "Application Support" not in script


def test_pkg_uninstall_helper_only_removes_the_canonical_launch_agent():
    script = _UNINSTALL_HELPER.read_text(encoding="utf-8")
    contents = build_launch_agent_plist(
        LAUNCH_AGENT_LABEL,
        ["/Applications/ShotQuill.app/Contents/MacOS/ShotQuill"],
    ).encode("utf-8")
    digest = hashlib.sha256(contents).hexdigest()

    assert f'CANONICAL_LAUNCH_AGENT_SHA256="{digest}"' in script


@pytest.mark.skipif(sys.platform == "win32", reason="macOS helper requires Bash")
def test_pkg_uninstall_helper_has_valid_bash_syntax():
    result = subprocess.run(
        ["bash", "-n", str(_UNINSTALL_HELPER)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
