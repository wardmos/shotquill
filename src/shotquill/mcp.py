# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""A minimal MCP (Model Context Protocol) server over stdio.

Hand-rolled on purpose: the four JSON-RPC methods an MCP tool server needs
(``initialize``, ``tools/list``, ``tools/call``, ``ping``) fit in a couple of
hundred lines, which buys zero extra dependencies and a server that runs (and
is tested) on every interpreter the rest of the package supports. Tools wrap
the same :mod:`shotquill.headless` calls as the CLI.

Security posture (deliberate):

- stdio only — no socket, no port. The OS pipe means only the parent process
  (the MCP client that spawned us) can talk to this server, and the session
  dies with it. ``--timeout`` can bound it further.
- macOS attributes Screen Recording to that parent (the agent host), so the
  TCC consent dialog names the real controller.
- Every tool call that touches the screen is audit-logged with ``via: mcp``.

Transport framing is newline-delimited JSON-RPC 2.0 (one message per line,
UTF-8). Tool failures are reported in-band (``isError`` results) so agents
can read them; protocol-level errors use JSON-RPC error responses.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

from shotquill import __version__, audit, command_spec, headless, record
from shotquill.capture.base import Rect

# Newest first: initialize echoes the client's version when we actually
# support it, otherwise offers our newest (per the MCP negotiation rules) —
# blindly echoing would claim conformance with protocols this server has
# never seen.
_SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

_INSTRUCTIONS = (
    "Screenshot & OCR tools for this machine. `capture` returns the image "
    "inline — pass max_width (e.g. 1024) to downscale and save context. "
    "`window_list` finds window ids for exact picks; `display_list` finds "
    "monitor indexes for one-monitor shots; `ocr` reads on-screen text "
    "without spending image tokens; `doctor` explains any unavailable "
    "capability or missing permission. To leave a reviewable trail of what "
    "you did on screen, call `session_start` once to get a handle, pass that "
    "handle as `session` to `session_frame` before/after each key action (it "
    "captures to disk, not into your context) — and optionally to `capture` to "
    "also file what you saw — then `session_end` to write an HTML filmstrip."
)


def serve(stdin=None, stdout=None, session_timeout: int | None = None) -> int:
    """Run the stdio server until EOF (or ``session_timeout`` seconds)."""
    fin = stdin if stdin is not None else sys.stdin
    fout = stdout if stdout is not None else sys.stdout
    if session_timeout:
        _arm_session_timeout(session_timeout)
    for line in fin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            _write(fout, _error(None, -32700, "parse error"))
            continue
        response = _handle(message)
        if response is not None:
            _write(fout, response)
    return 0


def _arm_session_timeout(seconds: int) -> None:
    """Hard-bound the session: opt-in consent should expire, not linger."""
    import signal

    def _expire(signum, frame):
        print(f"squill mcp: session timeout ({seconds}s) reached; exiting", file=sys.stderr)
        raise SystemExit(0)

    signal.signal(signal.SIGALRM, _expire)
    signal.alarm(seconds)


def _write(fout, payload: dict) -> None:
    fout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    fout.flush()


def _result(msg_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _handle(message) -> dict | None:
    # Single fail-safe boundary: any handler that raises an unexpected error
    # (e.g. a corrupt artifact whose ``read_text`` throws UnicodeDecodeError,
    # which is a ValueError and so escapes the per-tool ``OSError`` guards)
    # becomes an in-band JSON-RPC error rather than killing the serve loop and
    # ending the session for every later call. Tool failures are already
    # reported in-band by ``_tools_call``; this catches the rest.
    msg_id = message.get("id") if isinstance(message, dict) else None
    try:
        return _dispatch(message)
    except Exception:  # noqa: BLE001 - one bad message must not end the session
        if msg_id is None:
            return None  # notification (or unparseable id): no response is owed
        return _error(msg_id, -32603, "internal error")


def _dispatch(message) -> dict | None:
    if not isinstance(message, dict):
        return _error(None, -32600, "invalid request")
    msg_id = message.get("id")
    method = message.get("method")
    if not isinstance(method, str):
        return _error(msg_id, -32600, "invalid request") if msg_id is not None else None
    # A malformed params must answer with a JSON-RPC error (never one for a
    # notification, which gets no response of any kind), not kill the session:
    # one bad message from the client ending the whole server would break
    # every later call too.
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _error(msg_id, -32602, "params must be an object") if msg_id is not None else None
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in _SUPPORTED_PROTOCOLS else _SUPPORTED_PROTOCOLS[0]
        return _result(
            msg_id,
            {
                "protocolVersion": version,
                # Tools act on the screen; resources expose recorded sessions
                # (filmstrip / manifest / OTLP trace) for an agent or host to read
                # back without shelling out.
                "capabilities": {"tools": {}, "resources": {}},
                "serverInfo": {"name": "shotquill", "version": __version__},
                "instructions": _INSTRUCTIONS,
            },
        )
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": [tool["descriptor"] for tool in _TOOLS.values()]})
    if method == "tools/call":
        return _tools_call(msg_id, params)
    if method == "resources/list":
        return _result(msg_id, {"resources": _list_resources()})
    if method == "resources/read":
        return _read_resource(msg_id, params)
    if method.startswith("notifications/"):
        return None
    if msg_id is None:
        return None  # unknown notification: ignore per JSON-RPC
    return _error(msg_id, -32601, f"method not found: {method}")


def _tools_call(msg_id, params: dict) -> dict:
    name = params.get("name")
    tool = _TOOLS.get(name)
    if tool is None:
        return _error(msg_id, -32602, f"unknown tool: {name}")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        return _error(msg_id, -32602, "arguments must be an object")
    try:
        content, structured = tool["handler"](arguments)
        result = {"content": content, "isError": False}
        if structured is not None:
            # structuredContent mirrors the JSON already in the text blocks,
            # typed by the descriptor's outputSchema — agents read fields
            # instead of re-parsing JSON out of prose.
            result["structuredContent"] = structured
        return _result(msg_id, result)
    except Exception as exc:  # noqa: BLE001 - tool failures must stay in-band
        # Agents read tool output, not server stderr: report the failure as a
        # structured isError result they can branch on (mirrors CLI exit codes).
        payload = {"error": str(exc), "type": _error_type(exc)}
        hint = _ERROR_HINTS.get(payload["type"])
        if hint:
            payload["hint"] = hint
        return _result(
            msg_id,
            {
                "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                "isError": True,
            },
        )


# --- resources: recorded sessions --------------------------------------------
#
# Each session is exposed under `shotquill://session/<id>/<kind>`: the static
# HTML `filmstrip` (for a human), the `manifest` (structured trace), and the
# `otlp` projection. A live session offers only its manifest; the filmstrip and
# OTLP are written at session_end. Read-only — reads never touch the screen.

_RESOURCE_PREFIX = "shotquill://session/"
_RESOURCE_KINDS = {
    "filmstrip": ("text/html", "filmstrip_path", "static HTML filmstrip"),
    "manifest": ("application/json", "manifest_path", "session manifest (frames + spans)"),
    "otlp": ("application/json", "otlp_path", "OTLP/JSON trace projection"),
}


def _list_resources() -> list[dict]:
    resources: list[dict] = []
    for summary in record.list_sessions():
        kinds = ("filmstrip", "manifest", "otlp") if summary.status == "complete" else ("manifest",)
        for kind in kinds:
            mime, _, blurb = _RESOURCE_KINDS[kind]
            resources.append(
                {
                    "uri": f"{_RESOURCE_PREFIX}{summary.id}/{kind}",
                    "name": f"{summary.id} {kind}",
                    "title": f"{kind.capitalize()} — session {summary.id}",
                    "description": blurb,
                    "mimeType": mime,
                }
            )
    return resources


def _read_resource(msg_id, params: dict) -> dict:
    uri = params.get("uri")
    if not isinstance(uri, str) or not uri.startswith(_RESOURCE_PREFIX):
        return _error(msg_id, -32002, f"resource not found: {uri!r}")
    handle, _, kind = uri[len(_RESOURCE_PREFIX) :].rpartition("/")
    spec = _RESOURCE_KINDS.get(kind)
    if not handle or spec is None:
        return _error(msg_id, -32002, f"resource not found: {uri!r}")
    mime, attr, _ = spec
    # The handle must be a bare session id, never a path: a resource URI only ever
    # references ids we generated. Validating it here keeps a crafted
    # `shotquill://session/../../x/manifest` from reaching resolve_session's
    # path-handle branch and reading a file outside the records root.
    try:
        record.validate_session_id(handle)
        path = getattr(record.resolve_session(handle), attr)
        text = path.read_text(encoding="utf-8")
    except record.RecordError:
        return _error(msg_id, -32002, f"resource not found: {uri!r}")
    except OSError:
        # The session exists but this artifact isn't on disk — most often the
        # filmstrip/OTLP of a session that hasn't been ended yet. Don't echo the
        # OSError (it carries the local filesystem path).
        return _error(msg_id, -32002, f"resource not available: {uri!r} (is the session ended?)")
    return _result(msg_id, {"contents": [{"uri": uri, "mimeType": mime, "text": text}]})


def _error_type(exc: Exception) -> str:
    if isinstance(exc, headless.CapabilityUnsupported):
        return "unsupported"
    if isinstance(exc, (headless.WindowNotFound, headless.DisplayNotFound)):
        return "no_match"
    if isinstance(exc, (headless.CapturePermissionError, PermissionError)):
        return "permission"
    if isinstance(exc, headless.CaptureBlocked):
        return "blocked"
    if isinstance(exc, record.SessionNotFound):
        return "no_session"
    if isinstance(exc, (record.RecordError, ValueError, headless.ImageInputTooLarge)):
        return "invalid_arguments"
    return "error"


# The recovery step per error type — agents act on a next move far more
# reliably than on a bare failure message.
_ERROR_HINTS = {
    "unsupported": "call the doctor tool to see what this host supports",
    "no_match": "call window_list (or display_list) to see what is actually available",
    "permission": "call the doctor tool for the missing grant and how to fix it",
    "blocked": "the user's policy forbids this (the target is blocklisted, or an "
    "allowlist is active and the target is not on it, or a whole-screen capture was requested "
    "while the allowlist restricts to specific apps, or an export was gated on residual PII); "
    "do not retry",
    "no_session": "call session_start first, then pass the conversation_id it returns as `session`",
}


# --- tool handlers -----------------------------------------------------------


def _validate_target(args: dict) -> Rect | None:
    """Shared target validation; returns the parsed region (or None)."""
    window_id, app, region = args.get("window_id"), args.get("app"), args.get("region")
    display = args.get("display")
    if sum(value is not None for value in (window_id, app, region, display)) > 1:
        raise ValueError("window_id, app, region and display are mutually exclusive")
    if display is not None and (not isinstance(display, int) or isinstance(display, bool)):
        raise ValueError("display must be an integer index (see display_list)")
    if app is not None and not str(app).strip():
        # An empty app would silently fall through to a full-screen grab;
        # capturing what the caller did not ask for is worse than failing.
        raise ValueError("app must be a non-empty string")
    if args.get("title") is not None and app is None:
        raise ValueError("title only narrows app matches; pass app too")
    if region is None:
        return None
    if not isinstance(region, dict):
        raise ValueError("region must be an object {x, y, width, height}")
    try:
        rect = Rect(
            x=int(region["x"]),
            y=int(region["y"]),
            width=int(region["width"]),
            height=int(region["height"]),
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError("region must be an object {x, y, width, height} of integers") from None
    if rect.width <= 0 or rect.height <= 0:
        raise ValueError("region width/height must be positive")
    return rect


def _parse_rects(args: dict, key: str) -> list[Rect]:
    """Parse an optional array of ``{x, y, width, height}`` into logical rects.

    Coordinates are image-relative (the captured frame's own point space). Used
    for ``mask`` (blank these) and ``reveal`` (keep only these sharp).
    """
    raw = args.get(key) or []
    if not isinstance(raw, list):
        raise ValueError(f"{key} must be an array of {{x, y, width, height}} objects")
    rects: list[Rect] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(f"each {key} must be an object {{x, y, width, height}}")
        try:
            rect = Rect(
                x=int(item["x"]),
                y=int(item["y"]),
                width=int(item["width"]),
                height=int(item["height"]),
            )
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"each {key} must be {{x, y, width, height}} of integers") from None
        if rect.width <= 0 or rect.height <= 0:
            raise ValueError(f"{key} width/height must be positive")
        rects.append(rect)
    return rects


def _positive_int_or_zero(value, name: str) -> int:
    """Validate an optional non-negative integer arg (0/absent = the feature is off).

    ``bool`` is an ``int`` subclass, so without the explicit guard ``True`` would
    slip through as 1 — reject it like the ``max_width`` check does.
    """
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be a non-negative integer")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _confined_save_path(save_path: str) -> Path:
    """Resolve an agent-supplied capture ``save_path``, confined to the save folder.

    ``save_path`` comes straight from the model. Unlike the CLI's ``-o`` — which
    the user types and runs themselves — an agent-chosen path that can write image
    bytes anywhere on disk (overwriting dotfiles, dropping into ``~/.ssh``…) is a
    stronger primitive than screen capture and exactly the over-eager/injected
    agent the blocklist/allowlist defend against. So resolve it against, and
    confine it to, the user's configured ShotQuill save folder; a path that
    escapes that tree (``..``, an absolute path elsewhere, a symlink out) is
    refused. Relative paths are taken under the save folder.
    """
    from shotquill.config import Config

    base = Path(os.path.realpath(Path(Config().save_dir()).expanduser()))
    candidate = Path(str(save_path)).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    target = Path(os.path.realpath(candidate))
    if base not in target.parents:
        raise ValueError(
            "save_path must be inside the configured ShotQuill save folder; "
            "an agent-driven capture cannot write elsewhere"
        )
    return target


def _capture_image(
    args: dict,
    masks: list[Rect] | None = None,
    reveal: list[Rect] | None = None,
    redact_pii_recognizer=None,
):
    """Capture per the shared target args and return (QImage, target, matched).

    ``masks`` are caller rectangles painted out before the raw pixels become a
    QImage; ``reveal`` mosaics the whole frame, keeping only those rectangles
    sharp — so a hidden region never reaches the model, a file, or a frame. When
    ``redact_pii_recognizer`` is given, likely PII is OCR'd and masked after the
    caller masks but before the reveal mosaic."""
    region = _validate_target(args)
    capturer = headless.get_capturer()
    result, target, matched = headless.perform_capture(
        capturer,
        window_id=args.get("window_id"),
        app=args.get("app"),
        title=args.get("title"),
        region=region,
        display=args.get("display"),
        via="mcp",
    )
    result = headless.apply_masks(result, masks or [])
    if redact_pii_recognizer is not None:
        result = headless.redact_pii(result, redact_pii_recognizer)
    from shotquill.imaging import pixelate_except, result_to_qimage

    image = pixelate_except(result_to_qimage(result), reveal or [], result.scale)
    return image, target, matched


def _tool_capture(args: dict):
    fmt = args.get("format") or "png"
    if fmt not in ("png", "jpg", "jpeg"):
        raise ValueError(f"format must be png or jpg — got {fmt!r}")
    # Resolve the recognizer up front when redacting PII, so a host without OCR
    # fails fast before the capture.
    recognizer = headless.get_recognizer() if args.get("redact_pii") else None
    image, target, matched = _capture_image(
        args,
        _parse_rects(args, "mask"),
        _parse_rects(args, "reveal"),
        redact_pii_recognizer=recognizer,
    )

    max_width = args.get("max_width")
    if max_width is not None:
        # Mirror the CLI's `--max-width` check. `bool` is an `int` subclass, so
        # without the explicit guard `True` would slip through as width 1 and
        # silently emit a 1px-wide image instead of erroring.
        if isinstance(max_width, bool) or not isinstance(max_width, int):
            raise ValueError("max_width must be a positive integer")
        if max_width <= 0:
            raise ValueError("max_width must be positive")
        image = headless.downscale_to_width(image, max_width)

    data = headless.encode_qimage(image, fmt, deterministic=bool(args.get("deterministic")))
    meta = {"target": target, "width": image.width(), "height": image.height()}
    if matched > 1:
        meta["matched_windows"] = matched
        meta["note"] = "captured the front-most match; use window_id for an exact pick"

    dest = "inline"
    save_path = args.get("save_path")
    if save_path:
        path = _confined_save_path(str(save_path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        meta["saved_path"] = dest = str(path)

    # Pass `session` (a handle from session_start) to also file this capture as
    # an observation frame in that recording — the same explicit-handle contract
    # as the CLI's `capture --session`, deliberately not an ambient "current
    # session". The model still gets the (possibly downscaled) image above; the
    # archived copy goes through the record path.
    session_handle = args.get("session")
    if session_handle:
        meta["recorded"] = _mirror_observation(
            session_handle, image, target, dedup=bool(args.get("dedup"))
        )

    audit.record("capture", via="mcp", target=target, dest=dest)
    mime = "image/jpeg" if fmt in ("jpg", "jpeg") else "image/png"
    return [
        {"type": "image", "data": base64.b64encode(data).decode("ascii"), "mimeType": mime},
        {"type": "text", "text": json.dumps(meta, ensure_ascii=False)},
    ], meta


def _mirror_observation(session_handle: str, image, target: str, *, dedup: bool = False) -> dict:
    """File an ``observation`` frame into the named session; returns its meta.

    Two failure modes, deliberately split: an **unresolvable handle** is the
    caller's mistake and raises (becoming an in-band ``isError``, like the CLI's
    `capture --session ghost`). A handle that resolves but whose frame can't be
    archived (disk full, a write race) is a best-effort miss — reported in the
    returned ``error`` field, *not* raised, so the agent's successfully-captured
    image is still returned instead of being thrown away over an archival hiccup.
    """
    session = record.resolve_session(session_handle)  # bad handle -> isError (intended)
    blocklist = headless.active_blocklist()
    # Deterministic so a repeated glance archives byte-identically and `dedup`
    # can reference the previous frame instead of duplicating it.
    image_bytes = headless.encode_qimage(image, "png", deterministic=True)
    try:
        frame = record.record_frame(
            session,
            image_bytes=image_bytes,
            tool="observe",
            target=target,
            redacted=bool(blocklist),
            kind=record.KIND_OBSERVATION,
            dedup=dedup,
        )
    except (record.RecordError, OSError) as exc:
        return {"error": str(exc)}
    dest = str((session.dir / frame.image).resolve())
    audit.record("record_observation", via="record", target=target, dest=dest)
    return {"conversation_id": session.id, "index": frame.index, "image": dest}


def _tool_list_windows(args: dict):
    windows = headless.get_capturer().list_windows()
    audit.record("windows", via="mcp", target=f"{len(windows)} windows")
    payload = {"windows": headless.windows_payload(windows)}
    return [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], payload


def _tool_list_displays(args: dict):
    displays = headless.get_capturer().list_displays()
    audit.record("displays", via="mcp", target=f"{len(displays)} displays")
    payload = {"displays": headless.displays_payload(displays)}
    return [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], payload


def _tool_ocr(args: dict):
    recognizer = headless.get_recognizer()  # fail fast before any capture
    path = args.get("path")
    # A present-but-empty path is a mistake, not a request to OCR the screen:
    # treating it as "no path" would silently widen the read from a file to the
    # live screen — a different question than the agent asked. Match the CLI,
    # which keys the file-vs-capture branch on whether path was given at all.
    if path is not None and not (isinstance(path, str) and path.strip()):
        raise ValueError("path must be a non-empty string")
    if path is not None and any(
        args.get(key) is not None for key in ("window_id", "app", "title", "region", "display")
    ):
        # Silently OCRing the file while ignoring the capture target would
        # answer a different question than the agent asked.
        raise ValueError(
            "path and capture targets (window_id/app/title/region/display) are exclusive"
        )
    if path is not None:
        file_path = Path(str(path)).expanduser()
        with file_path.open("rb") as fh:
            data = headless.read_image_bytes(fh, label=str(file_path))
        image = headless.decode_qimage(data, label=str(file_path))
        source = str(file_path.resolve())
    else:
        # Capture-and-recognize in memory: only text reaches the agent, so a
        # "what does the screen say" question costs zero image tokens.
        image, source, _matched = _capture_image(args)
    # `boxes` adds per-line pixel boxes (and locates any assertion); without it
    # the tool stays text-only, as before.
    want_boxes = bool(args.get("boxes"))
    text_boxes = recognizer.recognize_boxes(image) if want_boxes else []
    lines = [b.text for b in text_boxes] if want_boxes else recognizer.recognize(image)
    audit.record("ocr", via="mcp", target=source)
    structured = {"lines": lines, "source": source}
    if want_boxes:
        structured["boxes"] = [
            {"text": b.text, "x": b.x, "y": b.y, "width": b.width, "height": b.height}
            for b in text_boxes
        ]

    # Optional assertions: turn "read the screen" into "check the screen". The
    # agent branches on structured `passed`, the way the CLI branches on its
    # exit code; a broken regex raises ValueError (-> invalid_arguments).
    contains = tuple(args.get("contains") or ())
    matches = tuple(args.get("matches") or ())
    if contains or matches:
        from shotquill import textassert

        ignore_case = bool(args.get("ignore_case"))
        if want_boxes:
            checks = textassert.evaluate_boxes(
                text_boxes, contains=contains, matches=matches, ignore_case=ignore_case
            )
        else:
            checks = textassert.evaluate(
                lines, contains=contains, matches=matches, ignore_case=ignore_case
            )
        assertions = []
        for c in checks:
            entry = {"kind": c.kind, "pattern": c.pattern, "passed": c.passed}
            # Where the match landed, as the same {x,y,w,h} a box reports — present
            # only when located (--boxes) and the check passed.
            rect = textassert.union_rect(c.boxes)
            if rect is not None:
                entry["box"] = {"x": rect[0], "y": rect[1], "width": rect[2], "height": rect[3]}
            assertions.append(entry)
        structured["assertions"] = assertions
        structured["passed"] = textassert.all_passed(checks)

    # OCR'd text is attacker-controllable (it comes off the screen) and an MCP
    # host may render this block in a terminal, so strip control chars the same
    # way the CLI does. The structured `lines` stay raw for programmatic use.
    text = "\n".join(headless.printable(line) for line in lines)
    return [{"type": "text", "text": text}], structured


def _tool_doctor(args: dict):
    payload = {"checks": headless.doctor_checks()}
    return [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], payload


def _tool_diff(args: dict):
    from shotquill import imaging

    threshold = _positive_int_or_zero(args.get("threshold"), "threshold")

    def _load(key: str):
        raw = args.get(key)
        if not raw or not str(raw).strip():
            raise ValueError(f"{key} is required (an image file path)")
        path = Path(str(raw)).expanduser()
        with path.open("rb") as fh:
            data = headless.read_image_bytes(fh, label=str(path))
        return headless.decode_qimage(data, label=str(path))

    a_img, b_img = _load("a"), _load("b")
    changed, box = imaging.image_diff_box(a_img, b_img, threshold=threshold)
    payload: dict = {
        "changed": changed,
        "a_size": {"width": a_img.width(), "height": a_img.height()},
        "b_size": {"width": b_img.width(), "height": b_img.height()},
    }
    if box is not None:
        payload["box"] = {"x": box[0], "y": box[1], "width": box[2], "height": box[3]}
    elif changed:
        payload["reason"] = "size differs"
    return [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], payload


def _tool_session_start(args: dict):
    directory = args.get("dir")
    session = record.start_session(
        session_id=args.get("id"),
        directory=Path(str(directory)).expanduser() if directory else None,
        agent_name=args.get("agent"),
        agent_id=args.get("agent_id"),
        label=args.get("label"),
    )
    audit.record("record_start", via="record", target=session.id, dest=str(session.dir))
    payload = {
        "conversation_id": session.id,
        "dir": str(session.dir.resolve()),
        "manifest": str(session.manifest_path.resolve()),
    }
    return [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], payload


def _require_session(args: dict) -> record.Session:
    """Resolve the required ``session`` handle (id or directory)."""
    handle = args.get("session")
    if not handle or not str(handle).strip():
        raise ValueError("session is required (the conversation_id from session_start)")
    return record.resolve_session(str(handle))


def _tool_session_frame(args: dict):
    session = _require_session(args)
    tool = args.get("tool")
    if not tool or not str(tool).strip():
        raise ValueError("tool is required (the action this frame documents)")

    # An asserting frame OCRs itself and records the verdict, so a failed check
    # becomes a frame in the trace. Resolve the recognizer first, so a host
    # without OCR fails before the capture rather than after.
    contains = tuple(args.get("contains") or ())
    matches = tuple(args.get("matches") or ())
    asserting = bool(contains or matches)
    scanning = bool(args.get("scan_pii"))
    redacting = bool(args.get("redact_pii"))
    recognizer = headless.get_recognizer() if (asserting or scanning or redacting) else None
    masks = _parse_rects(args, "mask")
    reveal = _parse_rects(args, "reveal")
    region = _validate_target(args)

    # Shared with the CLI record path (single-sourced in headless): blocklist
    # capture + masks/redaction/reveal/downscale/encode in one fixed order, so
    # the security-sensitive ordering can't drift between the two surfaces.
    # `blocklist` non-empty means protection was in force (see record.py).
    image, image_bytes, target, matched, blocklist = headless.render_recorded_frame(
        window_id=args.get("window_id"),
        app=args.get("app"),
        title=args.get("title"),
        region=region,
        display=args.get("display"),
        masks=masks,
        reveal=reveal,
        redact_recognizer=recognizer if redacting else None,
        max_dimension=_positive_int_or_zero(args.get("max_dimension"), "max_dimension"),
    )

    # OCR once and share the lines between the assertion and the PII scan.
    recognized = recognizer.recognize(image) if recognizer is not None else []
    assertions = None
    if asserting:
        from shotquill import textassert

        checks = textassert.evaluate(
            recognized,
            contains=contains,
            matches=matches,
            ignore_case=bool(args.get("ignore_case")),
        )
        assertions = [{"kind": c.kind, "pattern": c.pattern, "passed": c.passed} for c in checks]

    # Best-effort residual-risk flag: kind + count only, never the value. This
    # only flags; pass redact_pii to also mask the matched pixels (done above).
    pii_findings = None
    if scanning:
        from shotquill import pii

        pii_findings = [{"kind": f.kind, "count": f.count} for f in pii.scan(recognized)]

    frame = record.record_frame(
        session,
        image_bytes=image_bytes,
        tool=str(tool),
        target=target,
        label=args.get("label"),
        redacted=bool(blocklist),
        assertions=assertions,
        pii=pii_findings,
        phase=args.get("phase"),
        dedup=bool(args.get("dedup")),
    )
    dest = str((session.dir / frame.image).resolve())
    audit.record("record_frame", via="record", target=target, dest=dest)
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
    if matched > 1:
        payload["matched_windows"] = matched
        payload["note"] = "captured the front-most match; use window_id for an exact pick"
    return [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], payload


def _tool_session_end(args: dict):
    session = _require_session(args)
    # Compute before/after change boxes before the filmstrip renders. Best effort:
    # a diff hiccup must not block closing a session.
    try:
        headless.annotate_pair_diffs(session)
    except Exception:  # noqa: BLE001 - change boxes are a cosmetic review hint
        pass
    filmstrip = record.end_session(session)
    manifest = record.load_manifest(session)
    html_path = str(filmstrip.resolve())
    audit.record("record_end", via="record", target=session.id, dest=html_path)
    payload = {
        "conversation_id": session.id,
        "dir": str(session.dir.resolve()),
        "manifest": str(session.manifest_path.resolve()),
        "filmstrip": html_path,
        "otlp": str(session.otlp_path.resolve()),
        "frames": len(manifest.get("frames", [])),
    }
    return [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], payload


def _tool_session_export(args: dict):
    session = _require_session(args)
    manifest = record.load_manifest(session)
    fmt = args.get("format") or "tar.gz"
    if fmt not in ("tar.gz", "zip"):
        raise ValueError(f"format must be tar.gz or zip — got {fmt!r}")

    # Privacy gate (opt-in): refuse to bundle a trace that still carries residual
    # PII flags, so a flagged session isn't shared off the machine by accident.
    pii_totals = record.aggregate_pii(manifest)
    if bool(args.get("fail_on_pii")) and pii_totals:
        summary = ", ".join(f"{count} {kind}" for kind, count in sorted(pii_totals.items()))
        raise headless.CaptureBlocked(
            f"refusing to export: frames carry likely PII ({summary}); re-record with redaction"
        )

    out = args.get("output")
    archive = record.export_session(session, Path(str(out)).expanduser() if out else None, fmt=fmt)
    dest = str(archive.resolve())
    audit.record("record_export", via="record", target=session.id, dest=dest)
    payload = {
        "conversation_id": session.id,
        "archive": dest,
        "format": fmt,
        "frames": len(manifest.get("frames", [])),
    }
    if pii_totals:
        payload["pii"] = pii_totals
    return [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], payload


def _tool_session_list(args: dict):
    sessions = [
        {
            "conversation_id": s.id,
            "dir": str(s.dir.resolve()),
            "started_at": s.started_at,
            "status": s.status,
            "frames": s.frame_count,
            "size_bytes": s.size_bytes,
        }
        for s in record.list_sessions()
    ]
    audit.record("record_list", via="mcp", target=f"{len(sessions)} sessions")
    payload = {"sessions": sessions}
    return [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], payload


def _tool_session_prune(args: dict):
    max_age_days = args.get("max_age_days")
    max_sessions = args.get("max_sessions")
    if max_age_days is None and max_sessions is None:
        raise ValueError("session_prune needs max_age_days and/or max_sessions")
    if max_sessions is not None and (
        isinstance(max_sessions, bool) or not isinstance(max_sessions, int) or max_sessions < 0
    ):
        raise ValueError("max_sessions must be a non-negative integer")
    if max_age_days is not None and (
        isinstance(max_age_days, bool)
        or not isinstance(max_age_days, (int, float))
        or max_age_days < 0
    ):
        raise ValueError("max_age_days must be a non-negative number")

    dry_run = bool(args.get("dry_run"))
    removed = record.prune_sessions(
        max_age_days=max_age_days, max_sessions=max_sessions, dry_run=dry_run
    )
    if not dry_run:
        audit.record("record_prune", via="mcp", target=f"{len(removed)} sessions")
    payload = {
        "removed": [s.id for s in removed],
        "count": len(removed),
        "freed_bytes": sum(s.size_bytes for s in removed),
        "dry_run": dry_run,
    }
    return [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], payload


# --- tool descriptors --------------------------------------------------------
#
# The input schemas, names, descriptions, and annotations are generated from the
# single-source registry in :mod:`shotquill.command_spec`. Only the MCP-only
# ``outputSchema`` fragments live here (they have no CLI counterpart, so they
# cannot drift against one), keyed by the registry's ``mcp_name``.

OUTPUT_SCHEMAS = {
    "capture": {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "What was actually captured."},
            "width": {"type": "integer"},
            "height": {"type": "integer"},
            "matched_windows": {
                "type": "integer",
                "description": "Present when an app/title match was ambiguous.",
            },
            "note": {"type": "string"},
            "saved_path": {"type": "string"},
            "recorded": {
                "type": "object",
                "description": "Present when a `session` handle was passed, to also file this "
                "capture as an observation frame. Carries the frame's conversation_id/index/image "
                "on success, or an `error` string if archiving the frame failed (the image above "
                "is still returned).",
                "properties": {
                    "conversation_id": {"type": "string"},
                    "index": {"type": "integer"},
                    "image": {"type": "string"},
                    "error": {"type": "string"},
                },
            },
        },
        "required": ["target", "width", "height"],
    },
    "window_list": {
        "type": "object",
        "properties": {
            "windows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "owner": {"type": "string"},
                        "title": {"type": "string"},
                        "bundle_id": {"type": ["string", "null"]},
                        "bounds": {
                            "type": "object",
                            "properties": {
                                "x": {"type": "integer"},
                                "y": {"type": "integer"},
                                "width": {"type": "integer"},
                                "height": {"type": "integer"},
                            },
                        },
                    },
                },
            }
        },
        "required": ["windows"],
    },
    "display_list": {
        "type": "object",
        "properties": {
            "displays": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "name": {"type": "string"},
                        "primary": {"type": "boolean"},
                        "scale": {"type": "number"},
                        "bounds": {
                            "type": "object",
                            "properties": {
                                "x": {"type": "integer"},
                                "y": {"type": "integer"},
                                "width": {"type": "integer"},
                                "height": {"type": "integer"},
                            },
                        },
                    },
                },
            }
        },
        "required": ["displays"],
    },
    "ocr": {
        "type": "object",
        "properties": {
            "lines": {"type": "array", "items": {"type": "string"}},
            "boxes": {
                "type": "array",
                "description": "Present when `boxes` was set: one pixel box per line.",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                        "width": {"type": "integer"},
                        "height": {"type": "integer"},
                    },
                    "required": ["text", "x", "y", "width", "height"],
                },
            },
            "source": {
                "type": "string",
                "description": "The file or capture target the text came from.",
            },
            "passed": {
                "type": "boolean",
                "description": "Present when contains/matches were given: did all hold.",
            },
            "assertions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["contains", "matches"]},
                        "pattern": {"type": "string"},
                        "passed": {"type": "boolean"},
                        "box": {
                            "type": "object",
                            "description": "Where "
                            "the "
                            "match "
                            "landed "
                            "(pixels); "
                            "present "
                            "only "
                            "with "
                            "`boxes` "
                            "set and "
                            "the "
                            "check "
                            "passed.",
                            "properties": {
                                "x": {"type": "integer"},
                                "y": {"type": "integer"},
                                "width": {"type": "integer"},
                                "height": {"type": "integer"},
                            },
                            "required": ["x", "y", "width", "height"],
                        },
                    },
                    "required": ["kind", "pattern", "passed"],
                },
            },
        },
        "required": ["lines", "source"],
    },
    "session_start": {
        "type": "object",
        "properties": {
            "conversation_id": {"type": "string"},
            "dir": {"type": "string"},
            "manifest": {"type": "string"},
        },
        "required": ["conversation_id", "dir", "manifest"],
    },
    "session_frame": {
        "type": "object",
        "properties": {
            "conversation_id": {"type": "string"},
            "index": {"type": "integer"},
            "image": {"type": "string", "description": "Path the frame was written to."},
            "tool": {"type": "string"},
            "target": {"type": "string"},
            "redacted": {
                "type": "boolean",
                "description": "Blocklist protection was in force "
                "(not a no-user-content "
                "guarantee).",
            },
            "assertion_passed": {
                "type": "boolean",
                "description": "Present when contains/matches were given: did all hold.",
            },
            "assertions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["contains", "matches"]},
                        "pattern": {"type": "string"},
                        "passed": {"type": "boolean"},
                    },
                    "required": ["kind", "pattern", "passed"],
                },
            },
            "pii": {
                "type": "array",
                "description": "Present when scan_pii was set: "
                "best-effort PII flags, kind + count "
                "only (not a guarantee, never the "
                "value).",
                "items": {
                    "type": "object",
                    "properties": {"kind": {"type": "string"}, "count": {"type": "integer"}},
                    "required": ["kind", "count"],
                },
            },
            "phase": {
                "type": "string",
                "enum": ["before", "after"],
                "description": "Present when this frame is half of a before/after pair.",
            },
            "pair_id": {
                "type": "string",
                "description": "Links the two halves of a before/after pair.",
            },
            "matched_windows": {"type": "integer"},
            "note": {"type": "string"},
        },
        "required": ["conversation_id", "index", "image", "tool", "target", "redacted"],
    },
    "session_end": {
        "type": "object",
        "properties": {
            "conversation_id": {"type": "string"},
            "dir": {"type": "string"},
            "manifest": {"type": "string"},
            "filmstrip": {"type": "string"},
            "otlp": {
                "type": "string",
                "description": "OTLP/JSON trace projection (written to disk, not sent).",
            },
            "frames": {"type": "integer"},
        },
        "required": ["conversation_id", "dir", "manifest", "filmstrip", "otlp", "frames"],
    },
    "session_export": {
        "type": "object",
        "properties": {
            "conversation_id": {"type": "string"},
            "archive": {"type": "string", "description": "Path the archive was written to."},
            "format": {"type": "string"},
            "frames": {"type": "integer"},
            "pii": {
                "type": "object",
                "description": "Residual best-effort PII flags by kind (present only when any).",
                "additionalProperties": {"type": "integer"},
            },
        },
        "required": ["conversation_id", "archive", "format", "frames"],
    },
    "session_list": {
        "type": "object",
        "properties": {
            "sessions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "conversation_id": {"type": "string"},
                        "dir": {"type": "string"},
                        "started_at": {"type": ["string", "null"]},
                        "status": {"type": ["string", "null"]},
                        "frames": {"type": "integer"},
                        "size_bytes": {"type": "integer"},
                    },
                    "required": ["conversation_id", "dir", "frames", "size_bytes"],
                },
            }
        },
        "required": ["sessions"],
    },
    "session_prune": {
        "type": "object",
        "properties": {
            "removed": {"type": "array", "items": {"type": "string"}},
            "count": {"type": "integer"},
            "freed_bytes": {"type": "integer"},
            "dry_run": {"type": "boolean"},
        },
        "required": ["removed", "count", "freed_bytes", "dry_run"],
    },
    "doctor": {
        "type": "object",
        "properties": {
            "checks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "capability": {"type": "string"},
                        "available": {"type": "boolean"},
                        "detail": {"type": ["string", "null"]},
                    },
                    "required": ["capability", "available"],
                },
            }
        },
        "required": ["checks"],
    },
    "diff": {
        "type": "object",
        "properties": {
            "changed": {"type": "boolean"},
            "a_size": {
                "type": "object",
                "properties": {"width": {"type": "integer"}, "height": {"type": "integer"}},
            },
            "b_size": {
                "type": "object",
                "properties": {"width": {"type": "integer"}, "height": {"type": "integer"}},
            },
            "box": {
                "type": "object",
                "description": "Bounding box of the change (when located).",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "width": {"type": "integer"},
                    "height": {"type": "integer"},
                },
            },
            "reason": {"type": "string"},
        },
        "required": ["changed", "a_size", "b_size"],
    },
}


_HANDLERS = {
    "capture": _tool_capture,
    "window_list": _tool_list_windows,
    "display_list": _tool_list_displays,
    "ocr": _tool_ocr,
    "diff": _tool_diff,
    "doctor": _tool_doctor,
    "session_start": _tool_session_start,
    "session_frame": _tool_session_frame,
    "session_end": _tool_session_end,
    "session_list": _tool_session_list,
    "session_prune": _tool_session_prune,
    "session_export": _tool_session_export,
}

# name -> {"handler": ..., "descriptor": {...}}, generated so CLI and MCP can
# never disagree on a tool's name or inputs.
_TOOLS = command_spec.build_mcp_tools(_HANDLERS, OUTPUT_SCHEMAS)
