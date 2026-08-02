# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Regression tests for the macOS product package definition."""

from __future__ import annotations

import importlib.util
import plistlib
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DISTRIBUTION_MODULE = _REPO_ROOT / "packaging" / "macos" / "pkg_distribution.py"
_BUILD_SCRIPT = _REPO_ROOT / "packaging" / "macos" / "build_pkg.sh"
_COMPONENTS_PLIST = _REPO_ROOT / "packaging" / "macos" / "app_components.plist"
_CLI_PREINSTALL = _REPO_ROOT / "packaging" / "macos" / "scripts" / "cli" / "preinstall"
_TAP_SCRIPT = _REPO_ROOT / "packaging" / "macos" / "update_tap.sh"


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
        )
    )

    choices = _choices(root)
    assert choices["choice.app"].find("pkg-ref").attrib["id"] == "com.wardmos.shotquill.app"
    assert choices["choice.cli"].find("pkg-ref").attrib["id"] == "com.wardmos.shotquill.cli"

    packages = _package_refs(root)
    assert packages["com.wardmos.shotquill.app"].attrib["version"] == "0.1.0"
    assert packages["com.wardmos.shotquill.app"].text == "ShotQuill-app.pkg"
    assert packages["com.wardmos.shotquill.cli"].attrib["version"] == "0.1.0"
    assert packages["com.wardmos.shotquill.cli"].text == "ShotQuill-cli.pkg"
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
    assert "--install-location /Applications" in script
    assert "--install-location /usr/local/bin" in script
    assert '"$cli_root/shotquill"' in script
    assert '"$cli_root/squill"' in script
    assert "$cli_root/usr/local" not in script
    assert '--scripts "$cli_scripts"' in script


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


def test_homebrew_cask_installs_pkg_without_the_pkg_cli_component():
    script = _TAP_SCRIPT.read_text(encoding="utf-8")

    assert "ShotQuill-#{version}-#{arch}.pkg" in script
    assert 'pkg "ShotQuill-#{version}-#{arch}.pkg"' in script
    assert "allow_untrusted: true" in script
    assert '"choiceIdentifier" => "choice.cli"' in script
    assert '"attributeSetting" => 0' in script
    assert 'pkgutil: "com.wardmos.shotquill.app"' in script
    assert "#{appdir}" not in script
    assert 'binary "/Applications/ShotQuill.app/Contents/MacOS/ShotQuill"' in script
    assert 'target: "shotquill"' in script
    assert 'target: "squill"' in script
    assert "sudo: true" in script
    assert ".dmg" not in script
