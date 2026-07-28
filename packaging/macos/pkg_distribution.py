#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Render the Distribution XML for the ShotQuill macOS product package."""

from __future__ import annotations

import argparse
import re
from pathlib import PurePath
from xml.etree import ElementTree

_SAFE_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*\Z")


def _validate_version(version: str) -> None:
    if not _SAFE_VERSION.fullmatch(version):
        raise ValueError(f"unsafe package version: {version!r}")


def _validate_package_name(package: str) -> None:
    path = PurePath(package)
    if (
        not package
        or path.is_absolute()
        or len(path.parts) != 1
        or path.name != package
        or not package.endswith(".pkg")
    ):
        raise ValueError(f"unsafe component package name: {package!r}")


def render_distribution(*, version: str, app_package: str, cli_package: str) -> str:
    """Return a productbuild Distribution document for one release."""
    _validate_version(version)
    _validate_package_name(app_package)
    _validate_package_name(cli_package)

    root = ElementTree.Element("installer-gui-script", {"minSpecVersion": "2"})
    ElementTree.SubElement(root, "title").text = "ShotQuill"
    ElementTree.SubElement(
        root,
        "options",
        {
            "customize": "always",
            "require-scripts": "false",
        },
    )
    ElementTree.SubElement(
        root,
        "domains",
        {
            "enable_anywhere": "false",
            "enable_currentUserHome": "false",
            "enable_localSystem": "true",
        },
    )

    outline = ElementTree.SubElement(root, "choices-outline")
    ElementTree.SubElement(outline, "line", {"choice": "choice.app"})
    ElementTree.SubElement(outline, "line", {"choice": "choice.cli"})

    app_choice = ElementTree.SubElement(
        root,
        "choice",
        {
            "id": "choice.app",
            "title": "ShotQuill Application",
            "description": "Install ShotQuill in /Applications.",
            "start_selected": "true",
            "start_enabled": "false",
            "start_visible": "false",
        },
    )
    ElementTree.SubElement(
        app_choice,
        "pkg-ref",
        {"id": "com.wardmos.shotquill.app"},
    )

    cli_choice = ElementTree.SubElement(
        root,
        "choice",
        {
            "id": "choice.cli",
            "title": "Command Line Interface",
            "description": (
                "Optionally install shotquill and squill in /usr/local/bin. "
                "macOS may request administrator authorization for this system install."
            ),
            "start_selected": "false",
            "start_enabled": "true",
            "start_visible": "true",
        },
    )
    ElementTree.SubElement(
        cli_choice,
        "pkg-ref",
        {"id": "com.wardmos.shotquill.cli"},
    )

    app_ref = ElementTree.SubElement(
        root,
        "pkg-ref",
        {
            "id": "com.wardmos.shotquill.app",
            "version": version,
        },
    )
    app_ref.text = app_package

    cli_ref = ElementTree.SubElement(
        root,
        "pkg-ref",
        {
            "id": "com.wardmos.shotquill.cli",
            "version": version,
        },
    )
    cli_ref.text = cli_package

    ElementTree.indent(root, space="  ")
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ElementTree.tostring(
        root,
        encoding="unicode",
        short_empty_elements=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--app-package", required=True)
    parser.add_argument("--cli-package", required=True)
    args = parser.parse_args()
    print(
        render_distribution(
            version=args.version,
            app_package=args.app_package,
            cli_package=args.cli_package,
        )
    )


if __name__ == "__main__":
    main()
