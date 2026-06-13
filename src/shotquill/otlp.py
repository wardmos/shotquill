# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Project a recorded session into an OpenTelemetry trace, as OTLP/JSON.

The flight-recorder manifest (:mod:`shotquill.record`) is already shaped like an
OpenTelemetry GenAI trace; this module renders it into the wire format an OTel
backend actually ingests — OTLP/JSON — and writes it next to the session as a
plain file. **No network and no OpenTelemetry SDK dependency**: ShotQuill never
phones home (a hard privacy invariant), and the hand-rolled JSON keeps the zero
extra-dependency posture of the MCP server and the audit log. To ship the trace
to a collector, point your own OTel Collector at the file (the ``otlpjson`` /
``filelog`` receivers read exactly this), so the egress decision stays the
user's, made with their tools, not ShotQuill's.

Mapping (see shotquill_docs/feature-agent-flight-recorder.md):

- session  → one ``invoke_agent`` root span; ``gen_ai.conversation.id`` is the
  session id, ``gen_ai.agent.name`` / ``.id`` carry the agent.
- frame    → one ``execute_tool`` child span (``gen_ai.tool.name`` /
  ``.call.id``); the screenshot rides as a ``shotquill.frame`` span *event*
  whose attributes hold the image reference, redaction flag, label and target.
  OTLP does not inline binaries, so only the on-disk image path travels — which
  is exactly what keeps the pixels local.

Trace and span ids are derived deterministically from the session and tool-call
ids (a hash, not a random draw), so the same manifest always renders to the same
ids — re-running ``record end`` is idempotent, and tests need no clock or RNG.

The visual-frame extension lives in the ``shotquill.frame.*`` namespace because
OTel GenAI has no standard screenshot field yet; the semantic conventions are
still experimental, so this is pinned to one version and expected to churn.
"""

from __future__ import annotations

import datetime as dt
import hashlib

# The GenAI semantic-convention version these attribute names track. Bump it
# (and migrate the names) when intentionally moving to a newer convention — it
# is recorded on the resource so a consumer can tell which vintage it parsed.
SEMCONV_VERSION = "1.34.0"

# Span kind enum (OTLP). INTERNAL covers in-process agent/tool work; the value
# is the protobuf number, which OTLP/JSON carries as a bare integer.
_SPAN_KIND_INTERNAL = 1

OTLP_NAME = "trace.otlp.json"


def manifest_to_otlp(manifest: dict, *, service_version: str = "") -> dict:
    """Render a session manifest as an OTLP/JSON ``TracesData`` document.

    Returns a plain dict ready for :func:`json.dump`. The result is one
    ``resourceSpans`` entry holding the agent span and one child span per frame.
    """
    conversation_id = str(manifest.get("conversation_id", ""))
    agent = manifest.get("agent") or {}
    started = manifest.get("started_at")
    ended = manifest.get("ended_at") or started
    frames = manifest.get("frames", [])

    trace_id = _trace_id(conversation_id)
    root_span_id = _span_id(f"{conversation_id}/root")
    start_nanos = _iso_to_unix_nano(started)
    end_nanos = _iso_to_unix_nano(ended)

    root_attrs = [
        _str_attr("gen_ai.operation.name", "invoke_agent"),
        _str_attr("gen_ai.conversation.id", conversation_id),
    ]
    if agent.get("name"):
        root_attrs.append(_str_attr("gen_ai.agent.name", agent["name"]))
    if agent.get("id"):
        root_attrs.append(_str_attr("gen_ai.agent.id", agent["id"]))
    if manifest.get("label"):
        root_attrs.append(_str_attr("shotquill.session.label", manifest["label"]))

    spans = [
        {
            "traceId": trace_id,
            "spanId": root_span_id,
            "name": _agent_span_name(agent.get("name")),
            "kind": _SPAN_KIND_INTERNAL,
            "startTimeUnixNano": start_nanos,
            "endTimeUnixNano": end_nanos,
            "attributes": root_attrs,
        }
    ]
    for entry in frames:
        spans.append(_frame_span(entry, trace_id=trace_id, parent_span_id=root_span_id))

    resource_attrs = [_str_attr("service.name", "shotquill")]
    if service_version:
        resource_attrs.append(_str_attr("service.version", service_version))
    resource_attrs.append(_str_attr("shotquill.genai.semconv_version", SEMCONV_VERSION))

    return {
        "resourceSpans": [
            {
                "resource": {"attributes": resource_attrs},
                "scopeSpans": [
                    {
                        "scope": {"name": "shotquill", "version": service_version},
                        "spans": spans,
                    }
                ],
            }
        ]
    }


def _frame_span(entry: dict, *, trace_id: str, parent_span_id: str) -> dict:
    """Build the ``execute_tool`` span (with its frame event) for one frame."""
    span = entry.get("span") or {}
    tool_name = str(span.get("tool_name", ""))
    tool_call_id = str(span.get("tool_call_id", ""))
    at_nanos = _iso_to_unix_nano(entry.get("at"))

    attrs = [_str_attr("gen_ai.operation.name", "execute_tool")]
    if tool_name:
        attrs.append(_str_attr("gen_ai.tool.name", tool_name))
    if tool_call_id:
        attrs.append(_str_attr("gen_ai.tool.call.id", tool_call_id))

    # The screenshot is an event, not a span attribute: OTel does not inline
    # binaries, so only the on-disk reference travels.
    event_attrs = [_bool_attr("shotquill.frame.redacted", bool(entry.get("redacted")))]
    if entry.get("image"):
        event_attrs.append(_str_attr("shotquill.frame.image_ref", entry["image"]))
    if entry.get("label"):
        event_attrs.append(_str_attr("shotquill.frame.label", entry["label"]))
    if entry.get("target"):
        event_attrs.append(_str_attr("shotquill.frame.target", entry["target"]))

    return {
        "traceId": trace_id,
        "spanId": _span_id(tool_call_id or f"{trace_id}/{at_nanos}"),
        "parentSpanId": parent_span_id,
        "name": f"execute_tool {tool_name}".strip(),
        "kind": _SPAN_KIND_INTERNAL,
        "startTimeUnixNano": at_nanos,
        "endTimeUnixNano": at_nanos,
        "attributes": attrs,
        "events": [
            {
                "timeUnixNano": at_nanos,
                "name": "shotquill.frame",
                "attributes": event_attrs,
            }
        ],
    }


def _agent_span_name(agent_name: str | None) -> str:
    return f"invoke_agent {agent_name}".strip() if agent_name else "invoke_agent"


# --- value / id helpers ------------------------------------------------------


def _str_attr(key: str, value: object) -> dict:
    return {"key": key, "value": {"stringValue": str(value)}}


def _bool_attr(key: str, value: bool) -> dict:
    return {"key": key, "value": {"boolValue": bool(value)}}


def _trace_id(seed: str) -> str:
    """A stable 16-byte trace id (32 lowercase hex) derived from the seed."""
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def _span_id(seed: str) -> str:
    """A stable 8-byte span id (16 lowercase hex) derived from the seed."""
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _iso_to_unix_nano(iso: str | None) -> str:
    """Convert an ISO-8601 timestamp to a Unix-nanoseconds string (OTLP int64).

    OTLP/JSON carries 64-bit ints as strings. Our timestamps are second-
    resolution, so the arithmetic is exact integers (no float nanos drift); an
    unparseable or missing value maps to ``"0"`` rather than failing the export.
    """
    if not iso:
        return "0"
    try:
        moment = dt.datetime.fromisoformat(iso)
    except ValueError:
        return "0"
    return str(int(moment.timestamp()) * 1_000_000_000 + moment.microsecond * 1000)
