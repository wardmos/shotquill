# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Command-line front-end: thin argparse over :mod:`shotquill.headless`.

Contract (agents rely on it):

- ``squill`` with no arguments launches the menu-bar GUI (back-compat).
- ``squill capture`` writes one file and prints exactly one absolute path on
  stdout; everything else (warnings, progress) goes to stderr. ``--json``
  swaps the bare path for one JSON object (path, target, size, ambiguity)
  so nothing needs scraping off stderr. ``-o -`` streams the encoded image
  to stdout instead (refused on a TTY).
- ``squill ocr`` takes a file path, ``-`` for stdin, or the same target
  options as ``capture`` to recognize straight off the screen in one step.
- Exit codes: 0 ok, 1 other error, 2 usage, 3 permission denied,
  4 capability unavailable on this platform/session, 5 no window or display
  matched, 6 blocked by the app blocklist. They are printed in every
  ``--help`` so agents can discover them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from shotquill import __version__, audit, headless, paths

_EXIT_USAGE = 2

# Shown in every --help: agents discover the exit-code contract the same way
# they discover the flags, instead of needing the README.
_EXIT_CODE_EPILOG = (
    "exit codes: 0 ok, 1 error, 2 usage, 3 permission denied, "
    "4 capability unavailable on this platform/session, 5 no window or display "
    "matched, 6 blocked by the app blocklist"
)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    if not argv:
        # Bare invocation stays the GUI app, so the existing entry point and
        # double-click behaviour survive the CLI growing around them.
        from shotquill.app import run

        return run()

    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except headless.HeadlessError as exc:
        hint = " (run `squill doctor`)" if exc.exit_code == headless.EXIT_PERMISSION else ""
        print(f"squill: {exc}{hint}", file=sys.stderr)
        return exc.exit_code
    except PermissionError as exc:
        print(f"squill: permission denied: {exc} (run `squill doctor`)", file=sys.stderr)
        return headless.EXIT_PERMISSION
    except BrokenPipeError:
        # Downstream closed early (`squill capture -o - | head -c 100`); the
        # pipe-friendly contract means dying quietly, not with a traceback.
        # Repoint stdout at devnull so interpreter shutdown can flush safely.
        import os

        try:
            devnull_fd = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(devnull_fd, sys.stdout.fileno())
            finally:
                os.close(devnull_fd)
        except (OSError, ValueError):  # stdout without a real fd (tests, embeds)
            pass
        return 1
    except Exception as exc:  # noqa: BLE001 - the CLI boundary
        # Agents parse stderr and branch on exit codes; a traceback is noise
        # and an interpreter exit code is outside the documented contract.
        print(f"squill: error: {exc}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="squill",
        description="Screenshot & OCR for scripts and agents (run bare for the GUI).",
        epilog=_EXIT_CODE_EPILOG,
    )
    parser.add_argument("--version", action="version", version=f"shotquill {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    capture = sub.add_parser(
        "capture",
        help="capture the screen, a window, or a region",
        epilog=_EXIT_CODE_EPILOG,
    )
    _add_target_options(capture)
    capture.add_argument(
        "-o",
        "--output",
        help="output file path, or '-' for image bytes on stdout (default: temp dir)",
    )
    capture.add_argument("--format", choices=("png", "jpg"), default="png")
    capture.add_argument(
        "--max-width",
        type=int,
        metavar="PX",
        help="downscale to at most this many pixels wide (keeps aspect ratio)",
    )
    capture.add_argument(
        "--json",
        action="store_true",
        help="print JSON metadata (path, target, size, matches) instead of the bare path",
    )
    capture.add_argument(
        "--include-cursor", action="store_true", help="composite the pointer (best effort)"
    )
    capture.set_defaults(func=_cmd_capture)

    windows = sub.add_parser(
        "windows",
        help="list on-screen windows, front-most first",
        epilog=_EXIT_CODE_EPILOG,
    )
    windows.add_argument("--json", action="store_true", help="machine-readable output")
    windows.set_defaults(func=_cmd_windows)

    displays = sub.add_parser(
        "displays",
        help="list monitors and their indexes (for `capture --display N`)",
        epilog=_EXIT_CODE_EPILOG,
    )
    displays.add_argument("--json", action="store_true", help="machine-readable output")
    displays.set_defaults(func=_cmd_displays)

    ocr = sub.add_parser(
        "ocr",
        help="extract text from an image file, stdin, or straight off the screen (on-device)",
        epilog=_EXIT_CODE_EPILOG,
    )
    ocr.add_argument(
        "path",
        nargs="?",
        help=(
            "image file, or '-' for image bytes on stdin; omit to capture-and-"
            "recognize in one step (target options below pick what, like `capture`)"
        ),
    )
    _add_target_options(ocr)
    ocr.set_defaults(func=_cmd_ocr)

    doctor = sub.add_parser(
        "doctor",
        help="report platform capabilities and permissions",
        epilog=_EXIT_CODE_EPILOG,
    )
    doctor.add_argument("--json", action="store_true", help="machine-readable output")
    doctor.set_defaults(func=_cmd_doctor)

    mcp = sub.add_parser("mcp", help="serve the MCP stdio protocol (for AI agent hosts)")
    mcp.add_argument(
        "--timeout",
        type=int,
        metavar="SECONDS",
        help="exit after this many seconds (bound the session; default: until EOF)",
    )
    mcp.set_defaults(func=_cmd_mcp)

    install_desktop = sub.add_parser(
        "install-desktop-entry",
        help="install the Linux .desktop entry and icon under ~/.local/share",
        description=(
            "Copy the bundled .desktop launcher and icon to ~/.local/share so "
            "ShotQuill shows up in the GNOME / KDE / XFCE application menu. "
            "Needed after `pipx install shotquill`, because pipx puts data-files "
            "inside its private venv where the desktop never looks. Idempotent."
        ),
        epilog=_EXIT_CODE_EPILOG,
    )
    install_desktop.add_argument(
        "--print-paths",
        action="store_true",
        help="show the resolved source and destination paths, then exit (no copy)",
    )
    install_desktop.set_defaults(func=_cmd_install_desktop_entry)

    blocklist = sub.add_parser(
        "blocklist",
        help="manage the app blocklist (apps that are never captured)",
    )
    bl_sub = blocklist.add_subparsers(dest="blocklist_command", required=True)

    bl_list = bl_sub.add_parser("list", help="show the current rules")
    bl_list.add_argument("--json", action="store_true", help="machine-readable output")
    bl_list.set_defaults(func=_cmd_blocklist_list)

    bl_add = bl_sub.add_parser("add", help="add a rule")
    _add_blocklist_selector(bl_add)
    bl_add.set_defaults(func=_cmd_blocklist_add)

    bl_remove = bl_sub.add_parser("remove", help="remove a matching rule")
    _add_blocklist_selector(bl_remove)
    bl_remove.set_defaults(func=_cmd_blocklist_remove)

    return parser


def _add_blocklist_selector(command: argparse.ArgumentParser) -> None:
    """The bundle-id / name choice shared by ``blocklist add`` and ``remove``."""
    target = command.add_mutually_exclusive_group(required=True)
    target.add_argument("--bundle-id", help="match the owning app's bundle id exactly")
    target.add_argument("--name", help="match the app name as a case-insensitive substring")


def _add_target_options(command: argparse.ArgumentParser) -> None:
    """The shared what-to-capture options (`capture` and screen-`ocr`)."""
    target = command.add_mutually_exclusive_group()
    target.add_argument("--window-id", type=int, help="exact window id (see `squill windows`)")
    target.add_argument("--app", help="pick the front-most window of a matching app (substring)")
    target.add_argument("--region", help="logical-coordinate rectangle as x,y,w,h")
    target.add_argument(
        "--display",
        type=int,
        metavar="N",
        help="capture one monitor by index (see `squill displays`; 0 = primary)",
    )
    command.add_argument("--title", help="narrow --app matches by title substring")


def _usage_error(message: str) -> int:
    print(f"squill: {message}", file=sys.stderr)
    return _EXIT_USAGE


class _UsageError(Exception):
    """Raised by shared validation helpers; ``main`` paths turn it into exit 2."""


def _validate_target(args: argparse.Namespace):
    """Check the shared target options and return the parsed region (or None)."""
    if args.app is not None and not args.app.strip():
        # An empty --app is falsy and would silently fall through to a
        # full-screen grab — the one thing worse than failing is capturing
        # something the caller did not ask for.
        raise _UsageError("--app needs a non-empty app name")
    if args.title and not args.app:
        raise _UsageError("--title only narrows --app matches; pass --app too")
    if not args.region:
        return None
    try:
        return headless.parse_region(args.region)
    except ValueError as exc:
        raise _UsageError(str(exc)) from None


def _capture_image(args: argparse.Namespace, region, include_cursor: bool = False):
    """Run one capture and return ``(QImage, target, matched)``; warn on stderr
    when an app/title match was ambiguous, mirroring the MCP metadata."""
    capturer = headless.get_capturer(include_cursor=include_cursor)
    result, target, matched = headless.perform_capture(
        capturer,
        window_id=args.window_id,
        app=args.app,
        title=args.title,
        region=region,
        display=args.display,
    )
    if matched > 1 and not getattr(args, "json", False):
        # In --json mode the ambiguity rides along in the payload instead.
        print(
            f"squill: {matched} windows match; captured the front-most"
            " (use --window-id for an exact pick)",
            file=sys.stderr,
        )

    from shotquill.imaging import result_to_qimage

    return result_to_qimage(result), target, matched


def _cmd_capture(args: argparse.Namespace) -> int:
    if args.output == "-" and sys.stdout.isatty():
        return _usage_error("refusing to write image bytes to a terminal; pipe or redirect")
    if args.output == "-" and args.json:
        return _usage_error("--json owns stdout; it cannot be combined with `-o -`")
    if args.max_width is not None and args.max_width <= 0:
        return _usage_error("--max-width must be positive")

    try:
        region = _validate_target(args)
        image, target, matched = _capture_image(args, region, include_cursor=args.include_cursor)
    except _UsageError as exc:
        return _usage_error(str(exc))

    if args.max_width is not None:
        image = headless.downscale_to_width(image, args.max_width)

    if args.output == "-":
        sys.stdout.buffer.write(headless.encode_qimage(image, args.format))
        sys.stdout.buffer.flush()
        dest = "-"
    else:
        if args.output:
            path = Path(args.output).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            from shotquill.output.saver import build_output_path

            path = build_output_path(str(paths.capture_tmp_dir()), args.format)
        _save_image(image, path, args.format)
        dest = str(path.resolve())
        if args.json:
            meta = {
                "path": dest,
                "target": target,
                "width": image.width(),
                "height": image.height(),
            }
            if matched > 1:
                meta["matched_windows"] = matched
                meta["note"] = "captured the front-most match; use --window-id for an exact pick"
            print(json.dumps(meta, ensure_ascii=False))
        else:
            print(dest)

    audit.record("capture", via="cli", target=target, dest=dest)
    return 0


def _save_image(image, path: Path, format_hint: str) -> None:
    suffix = path.suffix.lower().lstrip(".")
    fmt = suffix if suffix in ("png", "jpg", "jpeg") else format_hint
    if suffix and suffix not in ("png", "jpg", "jpeg"):
        print(f"squill: unknown extension .{suffix}; writing {fmt} data", file=sys.stderr)
    if fmt in ("jpg", "jpeg"):
        from PySide6.QtGui import QImage

        # JPEG has no alpha; convert explicitly so the result is deterministic.
        image = image.convertToFormat(QImage.Format.Format_RGB888)
    if not image.save(str(path), fmt.upper()):
        raise OSError(f"failed to write {path}")


def _cmd_windows(args: argparse.Namespace) -> int:
    capturer = headless.get_capturer()
    windows = capturer.list_windows()
    if args.json:
        print(json.dumps(headless.windows_payload(windows), ensure_ascii=False))
    else:
        print(f"{'ID':>8}  {'OWNER':<24}{'BOUNDS':<22}TITLE")
        for w in windows:
            bounds = f"{w.bounds.x},{w.bounds.y} {w.bounds.width}x{w.bounds.height}"
            print(f"{w.window_id:>8}  {w.owner:<24}{bounds:<22}{w.title}")
    audit.record("windows", via="cli", target=f"{len(windows)} windows")
    return 0


def _cmd_displays(args: argparse.Namespace) -> int:
    displays = headless.get_capturer().list_displays()
    if args.json:
        print(json.dumps(headless.displays_payload(displays), ensure_ascii=False))
    else:
        print(f"{'INDEX':>5}  {'GEOMETRY':<22}{'SCALE':<7}NAME")
        for d in displays:
            geometry = f"{d.bounds.width}x{d.bounds.height} at {d.bounds.x},{d.bounds.y}"
            name = d.name + (" (primary)" if d.primary else "")
            print(f"{d.index:>5}  {geometry:<22}{d.scale:<7g}{name}")
    audit.record("displays", via="cli", target=f"{len(displays)} displays")
    return 0


def _cmd_ocr(args: argparse.Namespace) -> int:
    # Usage checks first, so a bad invocation is exit 2 even on a host where
    # OCR itself is unavailable (exit codes are the contract agents branch on).
    # --title counts as a capture target here: silently OCRing the file while
    # ignoring it would answer a different question than the caller asked.
    has_target = any(
        value is not None
        for value in (args.window_id, args.app, args.title, args.region, args.display)
    )
    if args.path is not None and has_target:
        return _usage_error("pass an image path or a capture target, not both")
    try:
        region = _validate_target(args)
    except _UsageError as exc:
        return _usage_error(str(exc))
    recognizer = headless.get_recognizer()  # fail fast before any capture

    if args.path is not None:
        if args.path == "-":
            data = headless.read_image_bytes(sys.stdin.buffer, label="stdin")
            source = "stdin"
        else:
            path = Path(args.path).expanduser()
            try:
                with path.open("rb") as fh:
                    data = headless.read_image_bytes(fh, label=str(path))
            except OSError as exc:
                print(f"squill: cannot read {args.path}: {exc}", file=sys.stderr)
                return 1
            source = str(path.resolve())

        from PySide6.QtGui import QImage

        image = QImage.fromData(data)
        if image.isNull():
            print(f"squill: {source} is not a decodable image", file=sys.stderr)
            return 1
    else:
        # Capture-and-recognize in memory (no file, no clipboard): one step
        # instead of `capture -o - | ocr -`, same as the MCP ocr tool.
        image, source, _matched = _capture_image(args, region)

    for line in recognizer.recognize(image):
        print(line)
    audit.record("ocr", via="cli", target=source)
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    checks = headless.doctor_checks()
    if args.json:
        print(json.dumps(checks, ensure_ascii=False))
    else:
        for item in checks:
            status = "ok" if item["available"] else "unavailable"
            detail = f"  ({item['detail']})" if item.get("detail") else ""
            print(f"{item['capability']:<20}{status}{detail}")
    return 0


def _cmd_mcp(args: argparse.Namespace) -> int:
    if args.timeout is not None and args.timeout <= 0:
        # 0 would silently mean "no timeout" (falsy) and a negative value
        # would blow up in signal.alarm — neither is a session bound.
        return _usage_error("--timeout must be a positive number of seconds")
    from shotquill.mcp import serve

    return serve(session_timeout=args.timeout)


def _cmd_blocklist_list(args: argparse.Namespace) -> int:
    from shotquill import blocklist as bl

    blocklist = bl.load()
    if args.json:
        print(json.dumps([r.as_dict() for r in blocklist.rules], ensure_ascii=False))
    elif not blocklist.rules:
        print("(empty)")
    else:
        for rule in blocklist.rules:
            print(rule.describe())
    return 0


def _cmd_blocklist_add(args: argparse.Namespace) -> int:
    from shotquill import blocklist as bl

    rule = bl.BlockRule(bundle_id=args.bundle_id, name=args.name)
    blocklist = bl.load()
    if rule in blocklist.rules:
        print(f"squill: {rule.describe()} is already on the blocklist", file=sys.stderr)
        return 0
    bl.save(bl.Blocklist(blocklist.rules + (rule,)))
    print(rule.describe())
    return 0


def _cmd_blocklist_remove(args: argparse.Namespace) -> int:
    from shotquill import blocklist as bl

    rule = bl.BlockRule(bundle_id=args.bundle_id, name=args.name)
    blocklist = bl.load()
    remaining = tuple(r for r in blocklist.rules if r != rule)
    if len(remaining) == len(blocklist.rules):
        print(f"squill: {rule.describe()} was not on the blocklist", file=sys.stderr)
        return 0
    bl.save(bl.Blocklist(remaining))
    return 0


# --- install-desktop-entry --------------------------------------------------
#
# Linux only. ``pipx install shotquill`` puts data-files inside its private
# venv (``~/.local/pipx/venvs/shotquill/share/...``), which is *not* on
# XDG_DATA_DIRS — so the app menu never sees them. This command bridges that
# by copying the bundled .desktop launcher and SVG icon into
# ``~/.local/share`` where every freedesktop desktop looks.

_DESKTOP_ENTRY_SUBPATH = ("share", "applications", "shotquill-gui.desktop")
_ICON_SVG_SUBPATH = (
    "share",
    "icons",
    "hicolor",
    "scalable",
    "apps",
    "shotquill.svg",
)


def _locate_packaged_data(subpath: tuple[str, ...]) -> Path | None:
    """Find a data-files entry shipped with the wheel.

    Searches ``sys.prefix`` (the venv root for pipx / regular venv installs)
    and ``site.USER_BASE`` (``pip install --user``), since setuptools writes
    data-files to ``<prefix>/share/...``. Returns the first existing path or
    ``None`` when neither layout has the file (e.g. an editable install with
    data-files staging skipped).
    """
    import site

    candidates: list[Path] = [Path(sys.prefix, *subpath)]
    user_base = site.getuserbase()
    if user_base:
        candidates.append(Path(user_base, *subpath))
    return next((c for c in candidates if c.is_file()), None)


def _xdg_data_home() -> Path:
    """``$XDG_DATA_HOME`` per the freedesktop Base Directory spec.

    Falls back to the spec default (``~/.local/share``) when unset, which is
    what every desktop reads as the user-scope data dir.
    """
    import os

    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def _refresh_desktop_caches(applications_dir: Path, icon_root: Path) -> None:
    """Best-effort: tell the desktop the new entry / icon exists *now*.

    Without ``update-desktop-database`` / ``gtk-update-icon-cache`` the user
    typically has to log out / in (or wait for the desktop's next scan) before
    the menu entry appears. Both tools are optional — missing them is fine,
    the entry still lands and works on the next scan; we just lose the "shows
    up instantly" win. Errors are swallowed: an unavailable cache tool is the
    user's reality, not something to fail the install over.
    """
    import shutil
    import subprocess

    for tool, args in (
        ("update-desktop-database", [str(applications_dir)]),
        ("gtk-update-icon-cache", ["-f", "-t", str(icon_root)]),
    ):
        binary = shutil.which(tool)
        if not binary:
            continue
        try:
            subprocess.run([binary, *args], check=False, capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass


def _cmd_install_desktop_entry(args: argparse.Namespace) -> int:
    """Copy the GUI .desktop and icon to ``~/.local/share``.

    Returns 4 (capability unavailable) on non-Linux and when the bundled data
    files can't be located — there's nothing to install on either, so callers
    shouldn't keep retrying. Idempotent: a second run overwrites the same
    files with identical content.
    """
    if not sys.platform.startswith("linux"):
        print(
            "squill: install-desktop-entry is Linux-only "
            "(this platform doesn't use freedesktop launchers)",
            file=sys.stderr,
        )
        return headless.EXIT_UNSUPPORTED

    desktop_src = _locate_packaged_data(_DESKTOP_ENTRY_SUBPATH)
    icon_src = _locate_packaged_data(_ICON_SVG_SUBPATH)
    if desktop_src is None or icon_src is None:
        print(
            "squill: bundled desktop files not found in this install — "
            "expected at <prefix>/share/applications and <prefix>/share/icons; "
            "are you running an editable install built without data-files?",
            file=sys.stderr,
        )
        return headless.EXIT_UNSUPPORTED

    data_home = _xdg_data_home()
    applications_dir = data_home / "applications"
    icon_dir = data_home / "icons" / "hicolor" / "scalable" / "apps"
    # The freedesktop spec resolves ``Icon=shotquill`` by basename across the
    # icon theme, so the destination file must be named ``shotquill.svg`` even
    # if the source name differed. Same idea for the launcher: ``shotquill.desktop``
    # is the canonical id (StartupWMClass / .desktop file ids both look for it).
    desktop_dst = applications_dir / "shotquill.desktop"
    icon_dst = icon_dir / "shotquill.svg"

    if args.print_paths:
        print(f"desktop: {desktop_src} -> {desktop_dst}")
        print(f"icon:    {icon_src} -> {icon_dst}")
        return 0

    import shutil

    applications_dir.mkdir(parents=True, exist_ok=True)
    icon_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(desktop_src, desktop_dst)
    shutil.copyfile(icon_src, icon_dst)
    desktop_dst.chmod(0o644)
    icon_dst.chmod(0o644)
    _refresh_desktop_caches(applications_dir, data_home / "icons")
    print(f"installed: {desktop_dst}")
    print(f"installed: {icon_dst}")
    return 0
