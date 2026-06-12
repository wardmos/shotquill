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
import sys
from pathlib import Path

from shotquill import __version__, audit, headless
from shotquill.capture.base import Rect

# Newest first: initialize echoes the client's version when we actually
# support it, otherwise offers our newest (per the MCP negotiation rules) —
# blindly echoing would claim conformance with protocols this server has
# never seen.
_SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

_INSTRUCTIONS = (
    "Screenshot & OCR tools for this machine. `capture` returns the image "
    "inline — pass max_width (e.g. 1024) to downscale and save context. "
    "`list_windows` finds window ids for exact picks; `list_displays` finds "
    "monitor indexes for one-monitor shots; `ocr` reads on-screen text "
    "without spending image tokens; `doctor` explains any unavailable "
    "capability or missing permission."
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
                "capabilities": {"tools": {}},
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


def _error_type(exc: Exception) -> str:
    if isinstance(exc, headless.CapabilityUnsupported):
        return "unsupported"
    if isinstance(exc, (headless.WindowNotFound, headless.DisplayNotFound)):
        return "no_match"
    if isinstance(exc, (headless.CapturePermissionError, PermissionError)):
        return "permission"
    if isinstance(exc, headless.CaptureBlocked):
        return "blocked"
    if isinstance(exc, ValueError):
        return "invalid_arguments"
    return "error"


# The recovery step per error type — agents act on a next move far more
# reliably than on a bare failure message.
_ERROR_HINTS = {
    "unsupported": "call the doctor tool to see what this host supports",
    "no_match": "call list_windows (or list_displays) to see what is actually available",
    "permission": "call the doctor tool for the missing grant and how to fix it",
    "blocked": "the target app is on the user's blocklist and will not be captured; do not retry",
}


# --- tool handlers -----------------------------------------------------------


def _validate_target(args: dict) -> Rect | None:
    """Shared target validation; returns the parsed region (or None)."""
    window_id, app, region = args.get("window_id"), args.get("app"), args.get("region")
    display = args.get("display")
    if sum(value is not None for value in (window_id, app, region, display)) > 1:
        raise ValueError("window_id, app, region and display are mutually exclusive")
    if display is not None and (not isinstance(display, int) or isinstance(display, bool)):
        raise ValueError("display must be an integer index (see list_displays)")
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


def _capture_image(args: dict):
    """Capture per the shared target args and return (QImage, target, matched)."""
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
    from shotquill.imaging import result_to_qimage

    return result_to_qimage(result), target, matched


def _tool_capture(args: dict):
    fmt = args.get("format") or "png"
    if fmt not in ("png", "jpg", "jpeg"):
        raise ValueError(f"format must be png or jpg — got {fmt!r}")
    image, target, matched = _capture_image(args)

    max_width = args.get("max_width")
    if max_width is not None:
        image = headless.downscale_to_width(image, int(max_width))

    data = headless.encode_qimage(image, fmt)
    meta = {"target": target, "width": image.width(), "height": image.height()}
    if matched > 1:
        meta["matched_windows"] = matched
        meta["note"] = "captured the front-most match; use window_id for an exact pick"

    dest = "inline"
    save_path = args.get("save_path")
    if save_path:
        path = Path(str(save_path)).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        meta["saved_path"] = dest = str(path.resolve())

    audit.record("capture", via="mcp", target=target, dest=dest)
    mime = "image/jpeg" if fmt in ("jpg", "jpeg") else "image/png"
    return [
        {"type": "image", "data": base64.b64encode(data).decode("ascii"), "mimeType": mime},
        {"type": "text", "text": json.dumps(meta, ensure_ascii=False)},
    ], meta


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
    if path and any(args.get(key) is not None for key in ("window_id", "app", "title", "region")):
        # Silently OCRing the file while ignoring the capture target would
        # answer a different question than the agent asked.
        raise ValueError("path and capture targets (window_id/app/title/region) are exclusive")
    if path:
        from PySide6.QtGui import QImage

        file_path = Path(str(path)).expanduser()
        image = QImage.fromData(file_path.read_bytes())
        if image.isNull():
            raise ValueError(f"{file_path} is not a decodable image")
        source = str(file_path.resolve())
    else:
        # Capture-and-recognize in memory: only text reaches the agent, so a
        # "what does the screen say" question costs zero image tokens.
        image, source, _matched = _capture_image(args)
    lines = recognizer.recognize(image)
    audit.record("ocr", via="mcp", target=source)
    structured = {"lines": lines, "source": source}
    return [{"type": "text", "text": "\n".join(lines)}], structured


def _tool_doctor(args: dict):
    payload = {"checks": headless.doctor_checks()}
    return [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], payload


# --- tool descriptors --------------------------------------------------------

_TARGET_PROPERTIES = {
    "window_id": {
        "type": "integer",
        "description": "Exact window id from list_windows. Mutually exclusive with app/region.",
    },
    "app": {
        "type": "string",
        "description": (
            "Case-insensitive substring of the owning app's name; the front-most "
            "matching window is captured (ambiguity is reported in the result)."
        ),
    },
    "title": {
        "type": "string",
        "description": "Narrow app matches by window-title substring (requires app).",
    },
    "region": {
        "type": "object",
        "description": (
            "Rectangle in logical screen coordinates. Mutually exclusive with window_id/app."
        ),
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "width": {"type": "integer"},
            "height": {"type": "integer"},
        },
        "required": ["x", "y", "width", "height"],
    },
    "display": {
        "type": "integer",
        "description": (
            "Capture one monitor by index from list_displays (0 = primary). "
            "Mutually exclusive with window_id/app/region."
        ),
    },
}

_WINDOW_SCHEMA = {
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
}


_DISPLAY_SCHEMA = {
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
}


def _read_only(title: str) -> dict:
    """Annotations for the tools that never write anything an agent host
    would want to gate on — lets hosts auto-approve them."""
    return {"title": title, "readOnlyHint": True, "openWorldHint": False}


_TOOLS = {
    "capture": {
        "handler": _tool_capture,
        "descriptor": {
            "name": "capture",
            "description": (
                "Take a screenshot of the full screen (default), one window "
                "(by window_id, or by app/title match), one monitor (by "
                "display index), or a region. Returns the image plus a JSON "
                "metadata text block. Use max_width (e.g. 1024) to downscale "
                "large screens and save context."
            ),
            # Not readOnlyHint: save_path can write (and overwrite) a file.
            "annotations": {"title": "Take a screenshot", "openWorldHint": False},
            "inputSchema": {
                "type": "object",
                "properties": {
                    **_TARGET_PROPERTIES,
                    "format": {"type": "string", "enum": ["png", "jpg"], "default": "png"},
                    "max_width": {
                        "type": "integer",
                        "description": "Downscale to at most this many pixels wide.",
                    },
                    "save_path": {
                        "type": "string",
                        "description": "Also write the image to this file path.",
                    },
                },
                "additionalProperties": False,
            },
            "outputSchema": {
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
                },
                "required": ["target", "width", "height"],
            },
        },
    },
    "list_windows": {
        "handler": _tool_list_windows,
        "descriptor": {
            "name": "list_windows",
            "description": (
                "List on-screen windows, front-most first: id, owning app, "
                "title, and bounds. Ids feed capture/ocr window_id. May be "
                "unavailable on some platforms (e.g. Wayland) — see doctor."
            ),
            "annotations": _read_only("List on-screen windows"),
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "outputSchema": {
                "type": "object",
                "properties": {"windows": {"type": "array", "items": _WINDOW_SCHEMA}},
                "required": ["windows"],
            },
        },
    },
    "list_displays": {
        "handler": _tool_list_displays,
        "descriptor": {
            "name": "list_displays",
            "description": (
                "List the monitors of this machine: index (primary first), "
                "name, logical bounds on the virtual desktop, pixel scale. "
                "Indexes feed capture/ocr `display` for a one-monitor shot."
            ),
            "annotations": _read_only("List monitors"),
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "outputSchema": {
                "type": "object",
                "properties": {"displays": {"type": "array", "items": _DISPLAY_SCHEMA}},
                "required": ["displays"],
            },
        },
    },
    "ocr": {
        "handler": _tool_ocr,
        "descriptor": {
            "name": "ocr",
            "description": (
                "Extract text with on-device OCR. Pass path for an existing "
                "image file, or the capture target arguments (none = full "
                "screen) to capture-and-recognize in memory — only text is "
                "returned, costing no image tokens."
            ),
            "annotations": _read_only("Read text off the screen or an image"),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Image file to recognize. Exclusive with the capture targets."
                        ),
                    },
                    **_TARGET_PROPERTIES,
                },
                "additionalProperties": False,
            },
            "outputSchema": {
                "type": "object",
                "properties": {
                    "lines": {"type": "array", "items": {"type": "string"}},
                    "source": {
                        "type": "string",
                        "description": "The file or capture target the text came from.",
                    },
                },
                "required": ["lines", "source"],
            },
        },
    },
    "doctor": {
        "handler": _tool_doctor,
        "descriptor": {
            "name": "doctor",
            "description": (
                "Report this host's capability/permission matrix (capture, "
                "list_windows, ocr, screen-recording permission) with reasons "
                "for anything unavailable."
            ),
            "annotations": _read_only("Capability & permission report"),
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "outputSchema": {
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
        },
    },
}
