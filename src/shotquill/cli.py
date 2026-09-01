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
  matched, 6 blocked by the blocklist or not on the allowlist, 7 invalid input.
  They are printed in every ``--help`` so agents can discover them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from shotquill import __version__, audit, command_spec, debug_log, headless, paths

_LOG = debug_log.get_logger(__name__)

_EXIT_USAGE = 2

# Exit codes split into two bands so a caller can always tell *the operation
# failed* from *the operation succeeded and the answer is "no"*:
#   1-19   errors (the command could not do its job)
#   20+    assertion / predicate results (it ran fine; the asserted condition
#          was false)
# `ocr --contains/--matches` failing is the latter — the tool ran, the text just
# was not on screen. Keeping it out of the error band lets CI branch cleanly
# (`rc >= 20` = assertion false, `0 < rc < 20` = broken tool) and grow either
# band without collision. 20 also dodges the shell-reserved codes (126+, 255).
_EXIT_ASSERTION_FAILED = 20


def _debug_config():
    try:
        from shotquill.config import Config

        return Config()
    except Exception:
        return None


def _argv_summary(argv: list[str]) -> tuple[str, int, list[str]]:
    command = argv[0] if argv else "gui"
    flags = sorted({arg.split("=", 1)[0] for arg in argv if arg.startswith("-")})
    return command, len(argv), flags


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    debug_log.configure(_debug_config())
    command, argc, flags = _argv_summary(argv)
    _LOG.debug("cli main command=%s argc=%s flags=%s", command, argc, flags)
    if not argv:
        # Bare invocation stays the GUI app, so the existing entry point and
        # double-click behaviour survive the CLI growing around them.
        from shotquill.app import run

        return run()

    parser = command_spec.build_argparse(__version__, _handlers())
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except headless.HeadlessError as exc:
        _LOG.exception("headless error exit_code=%s", exc.exit_code)
        hint = " (run `squill doctor`)" if exc.exit_code == headless.EXIT_PERMISSION else ""
        print(f"squill: {exc}{hint}", file=sys.stderr)
        return exc.exit_code
    except PermissionError as exc:
        _LOG.exception("permission error")
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
        _LOG.exception("unhandled cli error")
        print(f"squill: error: {exc}", file=sys.stderr)
        return 1


def _handlers() -> dict:
    """Map each registry command's ``handler`` key to its callable.

    The argparse tree itself is generated from :mod:`shotquill.command_spec`;
    this is the only place the CLI binds command names to behaviour.
    """
    return {
        "capture": _cmd_capture,
        "window_list": _cmd_windows,
        "display_list": _cmd_displays,
        "ocr": _cmd_ocr,
        "diff": _cmd_diff,
        "doctor": _cmd_doctor,
        "session_start": _cmd_session_start,
        "session_frame": _cmd_session_frame,
        "session_end": _cmd_session_end,
        "session_list": _cmd_session_list,
        "session_prune": _cmd_session_prune,
        "session_export": _cmd_session_export,
        "mcp": _cmd_mcp,
        "uninstall": _cmd_uninstall,
        "desktop_install": _cmd_install_desktop_entry,
        "blocklist_list": _cmd_blocklist_list,
        "blocklist_add": _cmd_blocklist_add,
        "blocklist_remove": _cmd_blocklist_remove,
        "allowlist_list": _cmd_allowlist_list,
        "allowlist_add": _cmd_allowlist_add,
        "allowlist_remove": _cmd_allowlist_remove,
        "allowlist_enable": _cmd_allowlist_enable,
        "allowlist_disable": _cmd_allowlist_disable,
    }


def _usage_error(message: str) -> int:
    print(f"squill: {message}", file=sys.stderr)
    return _EXIT_USAGE


class _UsageError(Exception):
    """Raised by shared validation helpers; ``main`` paths turn it into exit 2."""


def _flags_set(*pairs, present=lambda value: value is not None) -> list[str]:
    """Names of the ``(name, value)`` pairs whose value was actually supplied.

    Shared by the mutually-exclusive-flag checks. ``present`` defaults to
    "not None" (the right test for a target option); pass ``bool`` for repeatable
    lists / boolean flags where an empty list or ``False`` means "not given"."""
    return [name for name, value in pairs if present(value)]


def _validate_target(args: argparse.Namespace):
    """Check the shared target options and return the parsed region (or None)."""
    if args.app is not None and not args.app.strip():
        # An empty --app is falsy and would silently fall through to a
        # full-screen grab — the one thing worse than failing is capturing
        # something the caller did not ask for.
        raise _UsageError("--app needs a non-empty app name")
    if args.title and not args.app:
        raise _UsageError("--title only narrows --app matches; pass --app too")
    if args.region is None:
        return None
    # An explicit empty --region "" is falsy; let parse_region reject it rather
    # than silently falling through to a full-screen grab the caller didn't ask
    # for (same hazard guarded above for --app).
    try:
        return headless.parse_region(args.region)
    except ValueError as exc:
        raise _UsageError(str(exc)) from None


def _parse_rects(specs, flag: str):
    """Parse repeatable ``x,y,w,h`` specs into logical rectangles (or empty)."""
    rects = []
    for spec in specs or ():
        try:
            rects.append(headless.parse_region(spec))
        except ValueError as exc:
            raise _UsageError(f"{flag} {exc}") from None
    return rects


def _parse_masks(args: argparse.Namespace):
    """Parse repeatable ``--mask x,y,w,h`` into logical rectangles (or empty)."""
    return _parse_rects(getattr(args, "mask", None), "--mask")


def _parse_reveal(args: argparse.Namespace):
    """Parse repeatable ``--reveal x,y,w,h`` into logical rectangles (or empty)."""
    return _parse_rects(getattr(args, "reveal", None), "--reveal")


def _capture_image(
    args: argparse.Namespace,
    region,
    include_cursor: bool = False,
    masks=(),
    reveal=(),
    redact_pii_recognizer=None,
    operation_id: str | None = None,
):
    """Run one capture and return ``(QImage, target, matched)``; warn on stderr
    when an app/title match was ambiguous, mirroring the MCP metadata.

    ``masks`` are caller-supplied rectangles painted out before the frame leaves
    the raw-pixel stage, so the masked region never reaches the QImage, the
    file, or a recorded copy. ``reveal`` mosaics the whole frame, keeping only
    those rectangles sharp (minimize exposure to just the action). When
    ``redact_pii_recognizer`` is given, likely PII is OCR'd and masked after the
    caller masks but before the reveal mosaic, so the redaction joins the same
    raw-pixel stage."""
    operation_id = operation_id or debug_log.new_operation_id("cli-capture")
    _LOG.debug(
        "op=%s capture_image start interactive=%s window_id=%s app_set=%s title_set=%s region=%s "
        "display=%s masks=%s reveal=%s include_cursor=%s",
        operation_id,
        getattr(args, "interactive", False),
        args.window_id,
        args.app is not None,
        args.title is not None,
        region,
        args.display,
        len(masks or ()),
        len(reveal or ()),
        include_cursor,
    )
    capturer = headless.get_capturer(include_cursor=include_cursor)
    if getattr(args, "scrolling", False):
        # The frame count is dropped here so ``matched`` keeps its window-
        # ambiguity meaning (1 = unambiguous).
        result, target, _frames = headless.perform_scrolling_capture(
            capturer,
            region,
            via="cli",
            max_height=args.max_height,
            interval=args.scroll_interval,
        )
        matched = 1
    elif getattr(args, "interactive", False):
        # The compositor frames the shot and hands back the user's selection.
        # An enforcing allowlist refuses it (the picker can land on any window
        # or the whole screen — same contract as a fullscreen grab). An active
        # blocklist also refuses it because the already-composited selection
        # cannot be checked/redacted after the portal returns it.
        result, target, matched = headless.perform_interactive_capture(capturer, via="cli")
    else:
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

    result = headless.apply_masks(result, list(masks))
    if redact_pii_recognizer is not None:
        result = headless.redact_pii(result, redact_pii_recognizer)
    from shotquill.imaging import pixelate_except, result_to_qimage

    image = pixelate_except(result_to_qimage(result), list(reveal), result.scale)
    _LOG.debug(
        "op=%s capture_image complete target=%s matched=%s size=%sx%s",
        operation_id,
        target,
        matched,
        image.width(),
        image.height(),
    )
    return image, target, matched


def _cmd_capture(args: argparse.Namespace) -> int:
    operation_id = debug_log.new_operation_id("cli-capture")
    _LOG.debug("op=%s cmd_capture start", operation_id)
    if args.output == "-" and sys.stdout.isatty():
        return _usage_error("refusing to write image bytes to a terminal; pipe or redirect")
    if args.output == "-" and args.json:
        return _usage_error("--json owns stdout; it cannot be combined with `-o -`")
    if args.max_width is not None and args.max_width <= 0:
        return _usage_error("--max-width must be positive")
    if args.deterministic and args.include_cursor:
        # The pointer moves between frames, so compositing it is the opposite of
        # byte-stable; refusing is clearer than silently dropping one of them.
        return _usage_error("--deterministic and --include-cursor conflict; the cursor must be off")
    if args.interactive:
        # The picker chooses the target, so an explicit target option is a
        # contradiction — refuse it rather than silently honour one or the other.
        conflicts = _flags_set(
            ("--window-id", args.window_id),
            ("--app", args.app),
            ("--title", args.title),
            ("--region", args.region),
            ("--display", args.display),
        )
        if conflicts:
            return _usage_error(
                "--interactive picks the target itself; it conflicts with " + ", ".join(conflicts)
            )

    if args.scrolling:
        # The long screenshot scrolls within a framed --region, so the other target
        # modes contradict it.
        if args.interactive:
            return _usage_error("--scrolling and --interactive cannot be combined")
        target_conflicts = _flags_set(
            ("--window-id", args.window_id),
            ("--app", args.app),
            ("--title", args.title),
            ("--display", args.display),
        )
        if target_conflicts:
            return _usage_error(
                "--scrolling captures a --region; it conflicts with " + ", ".join(target_conflicts)
            )
        if not args.region:
            return _usage_error("--scrolling needs --region (the area to scroll within)")
        if args.max_height <= 0:
            return _usage_error("--max-height must be positive")
        if args.max_height > headless.SCROLL_MAX_HEIGHT_HARD_LIMIT:
            return _usage_error(
                f"--max-height cannot exceed {headless.SCROLL_MAX_HEIGHT_HARD_LIMIT}"
            )
        if args.scroll_interval <= 0:
            return _usage_error("--scroll-interval must be positive")

    # --deterministic forces the cursor off so the same scene always encodes the
    # same way; --include-cursor is rejected above, so this just stays the default.
    include_cursor = args.include_cursor and not args.deterministic
    # Resolve the recognizer up front when redacting PII, so a host without OCR
    # fails fast (exit 4) before the capture rather than after.
    recognizer = headless.get_recognizer() if args.redact_pii else None
    try:
        region = _validate_target(args)
        masks = _parse_masks(args)
        reveal = _parse_reveal(args)
        image, target, matched = _capture_image(
            args,
            region,
            include_cursor=include_cursor,
            masks=masks,
            reveal=reveal,
            redact_pii_recognizer=recognizer,
            operation_id=operation_id,
        )
    except _UsageError as exc:
        _LOG.debug("op=%s cmd_capture usage_error=%s", operation_id, exc)
        return _usage_error(str(exc))

    if args.max_width is not None:
        image = headless.downscale_to_width(image, args.max_width)

    # An explicit `--session` files this capture as an observation frame in that
    # recording session. The CLI keeps the handle explicit — no ambient
    # "current session" — so concurrent agents and CI stay safe.
    recorded = None
    if args.session:
        from shotquill import record

        try:
            recorded = _mirror_capture_observation(args.session, image, target, dedup=args.dedup)
        except record.RecordError as exc:
            _LOG.exception("op=%s record observation failed", operation_id)
            print(f"squill: {exc}", file=sys.stderr)
            return 1

    if args.output == "-":
        sys.stdout.buffer.write(
            headless.encode_qimage(image, args.format, deterministic=args.deterministic)
        )
        sys.stdout.buffer.flush()
        dest = "-"
    else:
        if args.output:
            path = Path(args.output).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            from shotquill.output.saver import build_output_path

            path = build_output_path(str(paths.capture_tmp_dir()), args.format)
        _save_image(image, path, args.format, deterministic=args.deterministic)
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
            if recorded is not None:
                meta["recorded"] = recorded
            print(json.dumps(meta, ensure_ascii=False))
        else:
            print(dest)
    if recorded is not None and not args.json:
        print(f"squill: recorded observation frame {recorded['index']}", file=sys.stderr)

    audit.record("capture", via="cli", target=target, dest=dest)
    _LOG.debug(
        "op=%s cmd_capture done target=%s dest_kind=%s recorded=%s",
        operation_id,
        target,
        "stdout" if dest == "-" else "file",
        recorded is not None,
    )
    return 0


def _mirror_capture_observation(
    session_handle: str, image, target: str, *, dedup: bool = False
) -> dict:
    """File an observation frame for a CLI `capture --session`; returns its meta."""
    from shotquill import record

    session = record.resolve_session(session_handle)
    blocklist = headless.active_blocklist()
    frame = record.record_frame(
        session,
        # Deterministic so an unchanged screen archives byte-identically and
        # --dedup can spot a repeated glance (the prime dedup target).
        image_bytes=headless.encode_qimage(image, "png", deterministic=True),
        tool="observe",
        target=target,
        redacted=bool(blocklist),
        kind=record.KIND_OBSERVATION,
        dedup=dedup,
    )
    dest = str((session.dir / frame.image).resolve())
    audit.record("record_observation", via="record", target=target, dest=dest)
    return {"conversation_id": session.id, "index": frame.index, "image": dest}


def _save_image(image, path: Path, format_hint: str, *, deterministic: bool = False) -> None:
    suffix = path.suffix.lower().lstrip(".")
    fmt = suffix if suffix in ("png", "jpg", "jpeg") else format_hint
    if suffix and suffix not in ("png", "jpg", "jpeg"):
        print(f"squill: unknown extension .{suffix}; writing {fmt} data", file=sys.stderr)
    if deterministic:
        # Encode to bytes through the same deterministic path as ``-o -`` (pinned
        # DPI, stripped timestamp/text chunks) and write those, so a file and a
        # piped capture of the same scene are byte-for-byte identical.
        path.write_bytes(headless.encode_qimage(image, fmt, deterministic=True))
        return
    if fmt in ("jpg", "jpeg"):
        from PySide6.QtGui import QImage

        # JPEG has no alpha; convert explicitly so the result is deterministic.
        image = image.convertToFormat(QImage.Format.Format_RGB888)
    if not image.save(str(path), fmt.upper()):
        raise OSError(f"failed to write {path}")


def _cmd_session_start(args: argparse.Namespace) -> int:
    from shotquill import record

    session = record.start_session(
        session_id=args.session_id,
        directory=Path(args.dir).expanduser() if args.dir else None,
        agent_name=args.agent,
        agent_id=args.agent_id,
        label=args.label,
    )
    audit.record("record_start", via="record", target=session.id, dest=str(session.dir))
    if args.json:
        print(
            json.dumps(
                {
                    "conversation_id": session.id,
                    "dir": str(session.dir.resolve()),
                    "manifest": str(session.manifest_path.resolve()),
                },
                ensure_ascii=False,
            )
        )
    else:
        # The directory is the handle for later commands, so it owns stdout
        # (mirroring `capture`'s one-path contract); the id is human info.
        print(f"squill: recording session {session.id}", file=sys.stderr)
        print(str(session.dir.resolve()))
    return 0


def _cmd_session_frame(args: argparse.Namespace) -> int:
    from shotquill import pii, record, textassert

    try:
        session = record.resolve_session(args.session)
        region = _validate_target(args)
        masks = _parse_masks(args)
        reveal = _parse_reveal(args)
    except record.RecordError as exc:
        print(f"squill: {exc}", file=sys.stderr)
        return 1
    except _UsageError as exc:
        return _usage_error(str(exc))

    # An asserting frame OCRs itself and records the verdict, so a failed test
    # becomes a frame in the trace. Resolve the recognizer first, so a host
    # without OCR fails before the capture rather than after.
    asserting = bool(args.contains or args.matches)
    scanning = bool(args.scan_pii)
    redacting = bool(args.redact_pii)
    recognizer = headless.get_recognizer() if (asserting or scanning or redacting) else None

    # The blocklist-enforced capture + redaction/reveal/downscale/encode pipeline
    # is shared with the MCP path (single-sourced in headless so the
    # security-sensitive ordering can't drift). `blocklist` non-empty means
    # protection was in force (recorded as `redacted` — see record.py).
    image, image_bytes, target, matched, blocklist = headless.render_recorded_frame(
        window_id=args.window_id,
        app=args.app,
        title=args.title,
        region=region,
        display=args.display,
        masks=masks,
        reveal=reveal,
        redact_recognizer=recognizer if redacting else None,
        max_dimension=args.max_dimension,
    )
    if matched > 1 and not args.json:
        print(
            f"squill: {matched} windows match; captured the front-most"
            " (use --window-id for an exact pick)",
            file=sys.stderr,
        )

    # Assert on the very pixels being filed (post-redaction), so the recorded
    # frame and its verdict always agree.
    # OCR once and share the lines between the assertion and the PII scan, so a
    # frame that both asserts and scans reads the very same recognized text.
    recognized = recognizer.recognize(image) if recognizer is not None else []
    checks: list[textassert.Check] = []
    assertions = None
    if asserting:
        try:
            checks = textassert.evaluate(
                recognized,
                contains=tuple(args.contains or ()),
                matches=tuple(args.matches or ()),
                ignore_case=args.ignore_case,
            )
        except ValueError as exc:
            return _usage_error(str(exc))  # a broken regex is the caller's bug
        assertions = [{"kind": c.kind, "pattern": c.pattern, "passed": c.passed} for c in checks]

    # Best-effort residual-risk flag: kind + count only, never the value. This
    # only flags; pass --redact-pii to also mask the matched pixels (done above).
    findings: list[pii.Finding] = []
    pii_findings = None
    if scanning:
        findings = pii.scan(recognized)
        pii_findings = [{"kind": f.kind, "count": f.count} for f in findings]

    try:
        frame = record.record_frame(
            session,
            image_bytes=image_bytes,
            tool=args.tool,
            target=target,
            label=args.label,
            redacted=bool(blocklist),
            assertions=assertions,
            pii=pii_findings,
            phase=args.phase,
            dedup=args.dedup,
        )
    except record.RecordError as exc:
        # e.g. an --after with no open --before — the caller's sequencing mistake.
        print(f"squill: {exc}", file=sys.stderr)
        return 1
    dest = str((session.dir / frame.image).resolve())
    audit.record("record_frame", via="record", target=target, dest=dest)
    if args.json:
        payload = {
            "conversation_id": session.id,
            "index": frame.index,
            "image": dest,
            "tool": frame.tool,
            "target": target,
            "redacted": frame.redacted,
        }
        if assertions is not None:
            payload["assertions"] = assertions
            payload["assertion_passed"] = frame.assertion_passed
        if pii_findings is not None:
            payload["pii"] = pii_findings
        if frame.phase is not None:
            payload["phase"] = frame.phase
            payload["pair_id"] = frame.pair_id
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(dest)
        for check in checks:
            print(textassert.describe(check), file=sys.stderr)
        if scanning:
            print(pii.describe(findings), file=sys.stderr)

    # A failed assertion is a result, not an error: the frame is still recorded
    # (capturing the failure is the point); the exit code carries the verdict.
    if frame.assertion_passed is False:
        return _EXIT_ASSERTION_FAILED
    return 0


def _cmd_session_end(args: argparse.Namespace) -> int:
    from shotquill import record

    try:
        session = record.resolve_session(args.session)
        # Compute before/after change boxes before rendering the filmstrip. Best
        # effort: a diff hiccup must never block closing a session, so swallow it.
        try:
            headless.annotate_pair_diffs(session)
        except Exception:  # noqa: BLE001 - change boxes are a cosmetic review hint
            pass
        filmstrip = record.end_session(session)
        manifest = record.load_manifest(session)
    except record.RecordError as exc:
        print(f"squill: {exc}", file=sys.stderr)
        return 1
    html_path = str(filmstrip.resolve())
    audit.record("record_end", via="record", target=session.id, dest=html_path)
    if args.json:
        print(
            json.dumps(
                {
                    "conversation_id": session.id,
                    "dir": str(session.dir.resolve()),
                    "manifest": str(session.manifest_path.resolve()),
                    "filmstrip": html_path,
                    "otlp": str(session.otlp_path.resolve()),
                    "frames": len(manifest.get("frames", [])),
                },
                ensure_ascii=False,
            )
        )
    else:
        print(html_path)
    return 0


def _cmd_session_export(args: argparse.Namespace) -> int:
    from shotquill import record

    try:
        session = record.resolve_session(args.session)
        manifest = record.load_manifest(session)
    except record.RecordError as exc:
        print(f"squill: {exc}", file=sys.stderr)
        return 1

    # Privacy gate (opt-in): refuse to bundle a trace that still carries residual
    # PII flags, so a flagged session isn't shared off the machine by accident.
    pii_totals = record.aggregate_pii(manifest)
    if args.fail_on_pii and pii_totals:
        summary = ", ".join(f"{count} {kind}" for kind, count in sorted(pii_totals.items()))
        print(
            f"squill: refusing to export: frames carry likely PII ({summary}); "
            "re-record with --redact-pii or drop --fail-on-pii to override",
            file=sys.stderr,
        )
        return headless.EXIT_BLOCKED

    try:
        out_path = record.export_session(
            session, Path(args.output).expanduser() if args.output else None, fmt=args.format
        )
    except record.RecordError as exc:
        print(f"squill: {exc}", file=sys.stderr)
        return 1
    dest = str(out_path.resolve())
    audit.record("record_export", via="record", target=session.id, dest=dest)
    if args.json:
        payload = {
            "conversation_id": session.id,
            "archive": dest,
            "format": args.format,
            "frames": len(manifest.get("frames", [])),
        }
        if pii_totals:
            payload["pii"] = pii_totals
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(dest)
    return 0


def _format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def _cmd_session_list(args: argparse.Namespace) -> int:
    from shotquill import record

    summaries = record.list_sessions()
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "conversation_id": s.id,
                        "dir": str(s.dir.resolve()),
                        "started_at": s.started_at,
                        "status": s.status,
                        "frames": s.frame_count,
                        "size_bytes": s.size_bytes,
                    }
                    for s in summaries
                ],
                ensure_ascii=False,
            )
        )
        return 0
    if not summaries:
        print("squill: no recorded sessions", file=sys.stderr)
        return 0
    print(f"{'STARTED':<25}{'STATUS':<11}{'FRAMES':>7}  {'SIZE':>8}  SESSION")
    for s in summaries:
        started = s.started_at or "?"
        print(
            f"{started:<25}{(s.status or '?'):<11}{s.frame_count:>7}  "
            f"{_format_size(s.size_bytes):>8}  {_printable(s.id)}"
        )
    return 0


def _cmd_session_prune(args: argparse.Namespace) -> int:
    from shotquill import record

    if args.max_age_days is None and args.max_sessions is None:
        return _usage_error("session prune needs --max-age-days and/or --max-sessions")
    removed = record.prune_sessions(
        max_age_days=args.max_age_days,
        max_sessions=args.max_sessions,
        dry_run=args.dry_run,
    )
    freed = sum(s.size_bytes for s in removed)
    if not args.dry_run:
        audit.record("record_prune", via="record", target=f"{len(removed)} sessions")
    if args.json:
        print(
            json.dumps(
                {
                    "removed": [s.id for s in removed],
                    "count": len(removed),
                    "freed_bytes": freed,
                    "dry_run": args.dry_run,
                },
                ensure_ascii=False,
            )
        )
        return 0
    verb = "would remove" if args.dry_run else "removed"
    for s in removed:
        print(f"{verb} {_printable(s.id)} ({_format_size(s.size_bytes)})", file=sys.stderr)
    print(f"{verb} {len(removed)} session(s), {_format_size(freed)}")
    return 0


# Strip control chars before printing app-supplied strings raw to the terminal;
# defined in headless so the CLI and MCP surfaces share one implementation.
_printable = headless.printable


def _cmd_windows(args: argparse.Namespace) -> int:
    capturer = headless.get_capturer()
    windows = capturer.list_windows()
    if args.json:
        print(json.dumps(headless.windows_payload(windows), ensure_ascii=False))
    else:
        print(f"{'ID':>8}  {'OWNER':<24}{'BOUNDS':<22}TITLE")
        for w in windows:
            bounds = f"{w.bounds.x},{w.bounds.y} {w.bounds.width}x{w.bounds.height}"
            print(f"{w.window_id:>8}  {_printable(w.owner):<24}{bounds:<22}{_printable(w.title)}")
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
    operation_id = debug_log.new_operation_id("cli-ocr")
    _LOG.debug(
        "op=%s cmd_ocr start path_set=%s boxes=%s assertions=%s",
        operation_id,
        args.path is not None,
        args.boxes,
        bool(args.contains or args.matches),
    )
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

        try:
            image = headless.decode_qimage(data, label=source)
        except ValueError as exc:
            print(f"squill: {exc}", file=sys.stderr)
            return 1
    else:
        # Capture-and-recognize in memory (no file, no clipboard): one step
        # instead of `capture -o - | ocr -`, same as the MCP ocr tool.
        image, source, _matched = _capture_image(args, region, operation_id=operation_id)

    # --boxes asks for per-line pixel boxes (and where assertions land); without
    # it the command keeps its plain text-lines contract.
    boxes = recognizer.recognize_boxes(image) if args.boxes else []
    lines = [box.text for box in boxes] if args.boxes else recognizer.recognize(image)
    for i, line in enumerate(lines):
        # OCR text is app-controlled (it's literally pixels off the screen), so
        # an attacker who owns what's on screen could smuggle terminal control
        # sequences through it — strip them like the windows table does before
        # printing. The raw `lines` still feed --contains/--matches below, and
        # the MCP path is JSON-escaped already.
        safe = _printable(line)
        print("{},{},{},{}\t{}".format(*boxes[i].as_rect(), safe) if args.boxes else safe)
    audit.record("ocr", via="cli", target=source)
    _LOG.debug(
        "op=%s cmd_ocr recognized lines=%s source_kind=%s",
        operation_id,
        len(lines),
        "stdin" if source == "stdin" else "file_or_capture",
    )

    # No --contains/--matches → the command just prints text (back-compat). With
    # them, the recognized text is asserted and the exit code carries the result.
    if not args.contains and not args.matches:
        return 0
    from shotquill import textassert

    try:
        # With --boxes, assert over the boxes so each verdict reports where it
        # landed; otherwise the text-only path.
        if args.boxes:
            checks = textassert.evaluate_boxes(
                boxes,
                contains=tuple(args.contains or ()),
                matches=tuple(args.matches or ()),
                ignore_case=args.ignore_case,
            )
        else:
            checks = textassert.evaluate(
                lines,
                contains=tuple(args.contains or ()),
                matches=tuple(args.matches or ()),
                ignore_case=args.ignore_case,
            )
    except ValueError as exc:
        return _usage_error(str(exc))  # a broken regex is the caller's bug
    for check in checks:
        print(textassert.describe(check), file=sys.stderr)
    return 0 if textassert.all_passed(checks) else _EXIT_ASSERTION_FAILED


def _cmd_diff(args: argparse.Namespace) -> int:

    from shotquill import imaging

    if args.a == "-" and args.b == "-":
        return _usage_error("only one of A/B can be stdin ('-')")
    if args.threshold < 0:
        return _usage_error("--threshold must be >= 0")

    def load(arg: str):
        if arg == "-":
            data = headless.read_image_bytes(sys.stdin.buffer, label="stdin")
            label = "stdin"
        else:
            path = Path(arg).expanduser()
            label = str(path)
            try:
                with path.open("rb") as fh:
                    data = headless.read_image_bytes(fh, label=label)
            except OSError as exc:
                print(f"squill: cannot read {arg}: {exc}", file=sys.stderr)
                return None
        try:
            return headless.decode_qimage(data, label=label)
        except ValueError as exc:
            print(f"squill: {exc}", file=sys.stderr)
            return None

    a_img = load(args.a)
    if a_img is None:
        return 1
    b_img = load(args.b)
    if b_img is None:
        return 1

    changed, box = imaging.image_diff_box(a_img, b_img, threshold=args.threshold)
    a_size = (a_img.width(), a_img.height())
    b_size = (b_img.width(), b_img.height())
    if args.json:
        payload: dict = {
            "changed": changed,
            "a_size": {"width": a_size[0], "height": a_size[1]},
            "b_size": {"width": b_size[0], "height": b_size[1]},
        }
        if box is not None:
            payload["box"] = {"x": box[0], "y": box[1], "width": box[2], "height": box[3]}
        elif changed:
            payload["reason"] = "size differs"
        print(json.dumps(payload, ensure_ascii=False))
    elif not changed:
        print("identical")
    elif box is None:
        print(f"differ: size {a_size[0]}x{a_size[1]} vs {b_size[0]}x{b_size[1]}")
    else:
        print("changed: {},{},{},{}".format(*box))
    # 0 = identical, 20 = differ (the predicate-result band, like an assertion).
    return 0 if not changed else _EXIT_ASSERTION_FAILED


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


def _cmd_uninstall(args: argparse.Namespace) -> int:
    if sys.platform != "darwin":
        print("squill: uninstall is currently available only for macOS packages", file=sys.stderr)
        return headless.EXIT_UNSUPPORTED

    from shotquill import uninstall

    plan = uninstall.prepare_uninstall_plan()
    print(uninstall.format_uninstall_plan(plan))
    if args.dry_run:
        return 0
    if not plan.can_execute:
        print("squill: this installation cannot be removed automatically", file=sys.stderr)
        return headless.EXIT_UNSUPPORTED
    if not sys.stdin.isatty():
        print("squill: uninstall requires an interactive terminal", file=sys.stderr)
        return _EXIT_USAGE
    if not args.yes:
        try:
            response = input("Uninstall ShotQuill and keep all user data? [y/N] ")
        except EOFError:
            return 1
        if response.strip().casefold() not in {"y", "yes"}:
            print("Cancelled.", file=sys.stderr)
            return 1
    return uninstall.execute_cli_uninstall(plan)


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


def _cmd_allowlist_list(args: argparse.Namespace) -> int:
    from shotquill import allowlist as al

    allowlist = al.load()
    if args.json:
        print(
            json.dumps(
                {"enabled": allowlist.enabled, "rules": [r.as_dict() for r in allowlist.rules]},
                ensure_ascii=False,
            )
        )
        return 0
    print(f"enabled: {'yes' if allowlist.enabled else 'no'}")
    if not allowlist.rules:
        print("(no rules)")
    else:
        for rule in allowlist.rules:
            print(rule.describe())
    if allowlist.enabled and not allowlist.rules:
        print("warning: enabled with no rules — nothing can be captured", file=sys.stderr)
    return 0


def _cmd_allowlist_add(args: argparse.Namespace) -> int:
    from shotquill import allowlist as al
    from shotquill import blocklist as bl

    rule = bl.BlockRule(bundle_id=args.bundle_id, name=args.name)
    allowlist = al.load()
    if rule in allowlist.rules:
        print(f"squill: {rule.describe()} is already on the allowlist", file=sys.stderr)
        return 0
    al.save(al.Allowlist(enabled=allowlist.enabled, rules=allowlist.rules + (rule,)))
    print(rule.describe())
    return 0


def _cmd_allowlist_remove(args: argparse.Namespace) -> int:
    from shotquill import allowlist as al
    from shotquill import blocklist as bl

    rule = bl.BlockRule(bundle_id=args.bundle_id, name=args.name)
    allowlist = al.load()
    remaining = tuple(r for r in allowlist.rules if r != rule)
    if len(remaining) == len(allowlist.rules):
        print(f"squill: {rule.describe()} was not on the allowlist", file=sys.stderr)
        return 0
    al.save(al.Allowlist(enabled=allowlist.enabled, rules=remaining))
    return 0


def _cmd_allowlist_enable(args: argparse.Namespace) -> int:
    from shotquill import allowlist as al

    allowlist = al.load()
    al.save(al.Allowlist(enabled=True, rules=allowlist.rules))
    # Enabling with no rules is a full lockdown — say so rather than let the user
    # discover it as every capture being refused.
    if not allowlist.rules:
        print(
            "squill: allowlist enabled, but it has no rules — nothing can be captured "
            "until you `squill allowlist add` an app",
            file=sys.stderr,
        )
    return 0


def _cmd_allowlist_disable(args: argparse.Namespace) -> int:
    from shotquill import allowlist as al

    allowlist = al.load()
    al.save(al.Allowlist(enabled=False, rules=allowlist.rules))
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
            "squill: desktop install is Linux-only "
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
