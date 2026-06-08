# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Command-line front-end: thin argparse over :mod:`shotquill.headless`.

Contract (agents rely on it):

- ``squill`` with no arguments launches the menu-bar GUI (back-compat).
- ``squill capture`` writes one file and prints exactly one absolute path on
  stdout; everything else (warnings, progress) goes to stderr. ``-o -``
  streams the encoded image to stdout instead (refused on a TTY).
- Exit codes: 0 ok, 1 other error, 2 usage, 3 permission denied,
  4 capability unavailable on this platform/session, 5 no window matched.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from shotquill import __version__, audit, headless, paths

_EXIT_USAGE = 2


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
        print(f"squill: {exc}", file=sys.stderr)
        return exc.exit_code
    except PermissionError as exc:
        print(f"squill: permission denied: {exc}", file=sys.stderr)
        return headless.EXIT_PERMISSION
    except BrokenPipeError:
        # Downstream closed early (`squill capture -o - | head -c 100`); the
        # pipe-friendly contract means dying quietly, not with a traceback.
        # Repoint stdout at devnull so interpreter shutdown can flush safely.
        import os

        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
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
    )
    parser.add_argument("--version", action="version", version=f"shotquill {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    capture = sub.add_parser("capture", help="capture the screen, a window, or a region")
    target = capture.add_mutually_exclusive_group()
    target.add_argument("--window-id", type=int, help="exact window id (see `squill windows`)")
    target.add_argument("--app", help="pick the front-most window of a matching app (substring)")
    target.add_argument("--region", help="logical-coordinate rectangle as x,y,w,h")
    capture.add_argument("--title", help="narrow --app matches by title substring")
    capture.add_argument(
        "-o",
        "--output",
        help="output file path, or '-' for image bytes on stdout (default: temp dir)",
    )
    capture.add_argument("--format", choices=("png", "jpg"), default="png")
    capture.add_argument(
        "--include-cursor", action="store_true", help="composite the pointer (best effort)"
    )
    capture.set_defaults(func=_cmd_capture)

    windows = sub.add_parser("windows", help="list on-screen windows, front-most first")
    windows.add_argument("--json", action="store_true", help="machine-readable output")
    windows.set_defaults(func=_cmd_windows)

    ocr = sub.add_parser("ocr", help="extract text from an image (on-device)")
    ocr.add_argument("path", help="image file, or '-' to read image bytes from stdin")
    ocr.set_defaults(func=_cmd_ocr)

    doctor = sub.add_parser("doctor", help="report platform capabilities and permissions")
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

    return parser


def _usage_error(message: str) -> int:
    print(f"squill: {message}", file=sys.stderr)
    return _EXIT_USAGE


def _cmd_capture(args: argparse.Namespace) -> int:
    if args.app is not None and not args.app.strip():
        # An empty --app is falsy and would silently fall through to a
        # full-screen grab — the one thing worse than failing is capturing
        # something the caller did not ask for.
        return _usage_error("--app needs a non-empty app name")
    if args.title and not args.app:
        return _usage_error("--title only narrows --app matches; pass --app too")
    if args.output == "-" and sys.stdout.isatty():
        return _usage_error("refusing to write image bytes to a terminal; pipe or redirect")

    region = None
    if args.region:
        try:
            region = headless.parse_region(args.region)
        except ValueError as exc:
            return _usage_error(str(exc))

    capturer = headless.get_capturer(include_cursor=args.include_cursor)
    result, target, matched = headless.perform_capture(
        capturer, window_id=args.window_id, app=args.app, title=args.title, region=region
    )
    if matched > 1:
        print(
            f"squill: {matched} windows match; captured the front-most"
            " (use --window-id for an exact pick)",
            file=sys.stderr,
        )

    from shotquill.imaging import result_to_qimage

    image = result_to_qimage(result)

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


def _cmd_ocr(args: argparse.Namespace) -> int:
    recognizer = headless.get_recognizer()
    if args.path == "-":
        data = sys.stdin.buffer.read()
        source = "stdin"
    else:
        try:
            data = Path(args.path).expanduser().read_bytes()
        except OSError as exc:
            print(f"squill: cannot read {args.path}: {exc}", file=sys.stderr)
            return 1
        source = str(Path(args.path).expanduser().resolve())

    from PySide6.QtGui import QImage

    image = QImage.fromData(data)
    if image.isNull():
        print(f"squill: {source} is not a decodable image", file=sys.stderr)
        return 1
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
    from shotquill.mcp import serve

    return serve(session_timeout=args.timeout)
