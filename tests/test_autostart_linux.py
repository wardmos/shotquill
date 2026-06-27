# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the Linux XDG autostart manager and platform factories."""

from __future__ import annotations

import pytest

from shotquill import autostart, hotkeys
from shotquill.autostart import linux as autostart_linux


@pytest.fixture
def autostart_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path / "config" / "autostart" / "shotquill.desktop"


def test_enable_writes_desktop_entry(autostart_home):
    mgr = autostart_linux.LinuxAutostartManager()
    assert mgr.is_enabled() is False
    mgr.enable()
    assert mgr.is_enabled() is True
    body = autostart_home.read_text(encoding="utf-8")
    assert "[Desktop Entry]" in body
    assert "X-GNOME-Autostart-enabled=true" in body
    assert "Exec=" in body


def test_desktop_entry_carries_display_metadata():
    # GNOME Tweaks / KDE Autostart / XFCE Session list these fields; without
    # them the entry shows up as a generic icon with no description, leaving
    # users unsure whether to keep it enabled.
    body = autostart_linux.build_autostart_desktop(["/usr/bin/shotquill"])
    assert "Icon=shotquill" in body
    assert "Comment=" in body
    assert "Categories=Graphics;Utility;" in body
    assert "GenericName=" in body
    # StartupNotify=false: a tray app doesn't open a window on launch, so the
    # cursor shouldn't show "starting" feedback (otherwise it looks broken).
    assert "StartupNotify=false" in body


def test_desktop_entry_display_metadata_values_are_non_empty():
    # ``Comment=`` / ``GenericName=`` / ``Categories=`` are no good if they're
    # present-but-empty — the autostart UI shows a blank description, exactly
    # the polish gap they exist to close. Parse keys and assert each carries
    # a non-blank value.
    body = autostart_linux.build_autostart_desktop(["/usr/bin/shotquill"])
    fields = dict(line.split("=", 1) for line in body.splitlines() if "=" in line)
    for key in ("Name", "GenericName", "Comment", "Categories", "Icon", "Exec", "Type"):
        assert fields.get(key, "").strip(), f"{key}= must be non-empty"


def test_desktop_entry_exec_line_renders_arguments():
    # The Exec= line is what XDG autostart actually launches. The renderer
    # must propagate every argv element through (so a wrapper invocation like
    # ``python -m shotquill`` isn't silently truncated to ``python``).
    body = autostart_linux.build_autostart_desktop(["/usr/bin/python3", "-m", "shotquill"])
    exec_line = next(line for line in body.splitlines() if line.startswith("Exec="))
    assert exec_line == "Exec=/usr/bin/python3 -m shotquill"


def test_desktop_entry_quotes_paths_with_spaces():
    # End-to-end version of the unit ``_exec_line`` test: the rendered
    # ``.desktop`` body must keep the quoting so XDG parses one argv element,
    # not three.
    body = autostart_linux.build_autostart_desktop(["/home/a b/ShotQuill.AppImage"])
    exec_line = next(line for line in body.splitlines() if line.startswith("Exec="))
    assert exec_line == 'Exec="/home/a b/ShotQuill.AppImage"'


def test_desktop_entry_doubles_literal_percent_in_exec():
    # A literal ``%`` in a path is a field code (``%f``/``%u``) to the launcher,
    # so it must be doubled or the entry is mis-launched at login. A bare path
    # with no other reserved char stays unquoted.
    body = autostart_linux.build_autostart_desktop(["/home/u%er/bin/python"])
    exec_line = next(line for line in body.splitlines() if line.startswith("Exec="))
    assert exec_line == "Exec=/home/u%%er/bin/python"


def test_gui_desktop_and_icon_files_ship_in_packaging():
    # Sanity guard for the two files pyproject's ``[tool.setuptools.data-files]``
    # references — a rename or accidental deletion would silently break the
    # ``pip install --user`` path (data-files reference resolves at sdist /
    # wheel build time, not import time, so the failure would only surface
    # at ``python -m build``).
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    desktop = root / "packaging" / "linux" / "shotquill-gui.desktop"
    icon_parts = ("packaging", "linux", "icons", "hicolor", "scalable", "apps", "shotquill.svg")
    icon = root.joinpath(*icon_parts)
    assert desktop.is_file(), f"missing GUI .desktop file at {desktop}"
    assert icon.is_file(), f"missing scalable icon at {icon}"
    # Cheap structural checks so a future "I'll just blank it" trip-wire fires
    # here instead of at the installer-side test.
    desktop_body = desktop.read_text(encoding="utf-8")
    assert desktop_body.startswith("[Desktop Entry]")
    assert "Terminal=false" in desktop_body  # GUI variant, not the AppImage CLI one
    assert "Exec=shotquill" in desktop_body
    from shotquill.desktop_id import LINUX_GUI_DESKTOP_FILE_NAME

    assert desktop.name == f"{LINUX_GUI_DESKTOP_FILE_NAME}.desktop"
    icon_body = icon.read_text(encoding="utf-8")
    assert icon_body.lstrip().startswith("<?xml") or icon_body.lstrip().startswith("<svg")


def test_desktop_entry_has_chinese_localised_metadata():
    # The app speaks en/zh in every other surface; the autostart entry should
    # match so a Chinese desktop session sees localised "通用名 / 注释" in the
    # startup-items UI instead of falling back to English. ``[zh_CN]`` is the
    # spec-defined locale suffix every freedesktop reader honours.
    body = autostart_linux.build_autostart_desktop(["/usr/bin/shotquill"])
    assert "GenericName[zh_CN]=" in body
    assert "Comment[zh_CN]=" in body
    fields = dict(line.split("=", 1) for line in body.splitlines() if "=" in line)
    assert fields["GenericName[zh_CN]"].strip(), "Chinese generic-name must be non-empty"
    assert fields["Comment[zh_CN]"].strip(), "Chinese comment must be non-empty"


def test_desktop_entry_parses_as_freedesktop_ini():
    # Every freedesktop-compliant reader (GNOME Tweaks, KDE Autostart, XFCE
    # Session, glib's GKeyFile) parses .desktop entries as case-sensitive INI.
    # Use Python's ``configparser`` as a stand-in: the body must have a
    # ``[Desktop Entry]`` group, every key=value must be a valid INI line, and
    # there must be no stray whitespace / continuation lines / duplicate keys
    # that would silently truncate the entry.
    import configparser

    body = autostart_linux.build_autostart_desktop(["/usr/bin/shotquill"])
    parser = configparser.RawConfigParser()
    # .desktop fields are case-sensitive (``Icon`` != ``icon``); RawConfigParser
    # lowercases by default, so override optionxform to preserve case.
    parser.optionxform = str
    parser.read_string(body)
    assert parser.sections() == ["Desktop Entry"]
    entry = parser["Desktop Entry"]
    assert entry["Type"] == "Application"
    assert entry["Name"] == "ShotQuill"
    assert entry["Exec"] == "/usr/bin/shotquill"
    assert entry["Icon"] == "shotquill"
    assert entry["Terminal"] == "false"
    # Categories is a freedesktop-spec multi-value field: every value must end
    # with ``;``, including the last one. Validate the form so a future
    # refactor that strips the trailing semicolon (a common mistake) trips
    # here instead of silently breaking KDE's parser.
    categories = entry["Categories"]
    assert categories.endswith(";"), f"Categories must end with ';'; got {categories!r}"
    parts = [p for p in categories.split(";") if p]
    assert "Graphics" in parts and "Utility" in parts


def test_disable_is_idempotent(autostart_home):
    mgr = autostart_linux.LinuxAutostartManager()
    mgr.enable()
    mgr.disable()
    assert mgr.is_enabled() is False
    mgr.disable()  # second disable must not raise


def test_set_enabled_toggles(autostart_home):
    mgr = autostart_linux.LinuxAutostartManager()
    mgr.set_enabled(True)
    assert mgr.is_enabled() is True
    mgr.set_enabled(False)
    assert mgr.is_enabled() is False


def test_launch_arguments_prefers_appimage_path_when_frozen(monkeypatch):
    monkeypatch.setattr(autostart_linux.sys, "frozen", True, raising=False)
    monkeypatch.setenv("APPIMAGE", "/opt/ShotQuill.AppImage")
    assert autostart_linux.launch_arguments() == ["/opt/ShotQuill.AppImage"]


def test_launch_arguments_dev_runs_module(monkeypatch):
    monkeypatch.setattr(autostart_linux.sys, "frozen", False, raising=False)
    args = autostart_linux.launch_arguments()
    assert args[1:] == ["-m", "shotquill"]


def test_exec_line_quotes_paths_with_spaces():
    line = autostart_linux._exec_line(["/home/a b/ShotQuill.AppImage"])
    assert line == '"/home/a b/ShotQuill.AppImage"'


def test_exec_line_escapes_reserved_chars_inside_quotes():
    # A path with a double-quote and a ``$`` must be quoted and the spec's
    # in-quote special chars backslash-escaped, so it can't break out of the
    # Exec= field or be reinterpreted by the launcher.
    line = autostart_linux._exec_line(['/home/a b/We"ird$dir/app'])
    assert line == '"/home/a b/We\\"ird\\$dir/app"'


def test_hotkeys_factory_routes_by_platform(monkeypatch):
    monkeypatch.setattr(hotkeys.sys, "platform", "linux")
    from shotquill.hotkeys.linux import LinuxHotkeyManager

    assert isinstance(hotkeys.get_manager(), LinuxHotkeyManager)


def test_autostart_factory_routes_by_platform(monkeypatch):
    monkeypatch.setattr(autostart.sys, "platform", "linux")
    assert isinstance(autostart.get_manager(), autostart_linux.LinuxAutostartManager)
