# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""OTLP/JSON projection tests: the manifest -> OpenTelemetry trace mapping.

Pure data maths — no Qt, no network, no OTel SDK. Feeds a manifest in and
asserts on the OTLP document shape an OTel backend would ingest.
"""

from __future__ import annotations

from shotquill import otlp


def _manifest() -> dict:
    return {
        "shotquill_manifest_version": 1,
        "conversation_id": "conv-otlp-1",
        "agent": {"name": "builder", "id": "agent-7"},
        "status": "complete",
        "label": "login flow",
        "started_at": "2026-06-13T10:00:00-04:00",
        "ended_at": "2026-06-13T10:00:05-04:00",
        "frames": [
            {
                "span": {"tool_name": "click", "tool_call_id": "conv-otlp-1/frame/1"},
                "at": "2026-06-13T10:00:03-04:00",
                "label": "click submit",
                "image": "frames/0001.png",
                "target": "window 33",
                "redacted": True,
            }
        ],
    }


def _attrs(attr_list: list[dict]) -> dict:
    """Flatten an OTLP attribute list to {key: scalar} for easy assertions."""
    out = {}
    for attr in attr_list:
        value = attr["value"]
        out[attr["key"]] = value.get("stringValue", value.get("boolValue"))
    return out


def _spans(document: dict) -> list[dict]:
    return document["resourceSpans"][0]["scopeSpans"][0]["spans"]


def test_root_span_maps_session_to_invoke_agent():
    document = otlp.manifest_to_otlp(_manifest(), service_version="1.2.3")
    root = _spans(document)[0]
    assert root["name"] == "invoke_agent builder"
    attrs = _attrs(root["attributes"])
    assert attrs["gen_ai.operation.name"] == "invoke_agent"
    assert attrs["gen_ai.conversation.id"] == "conv-otlp-1"
    assert attrs["gen_ai.agent.name"] == "builder"
    assert attrs["gen_ai.agent.id"] == "agent-7"
    assert attrs["shotquill.session.label"] == "login flow"


def test_frame_span_is_execute_tool_child_with_frame_event():
    document = otlp.manifest_to_otlp(_manifest())
    spans = _spans(document)
    root, frame = spans[0], spans[1]
    assert frame["name"] == "execute_tool click"
    assert frame["parentSpanId"] == root["spanId"]
    assert frame["traceId"] == root["traceId"]
    attrs = _attrs(frame["attributes"])
    assert attrs["gen_ai.tool.name"] == "click"
    assert attrs["gen_ai.tool.call.id"] == "conv-otlp-1/frame/1"

    (event,) = frame["events"]
    assert event["name"] == "shotquill.frame"
    event_attrs = _attrs(event["attributes"])
    assert event_attrs["shotquill.frame.redacted"] is True
    assert event_attrs["shotquill.frame.image_ref"] == "frames/0001.png"
    assert event_attrs["shotquill.frame.label"] == "click submit"
    assert event_attrs["shotquill.frame.target"] == "window 33"


def test_frame_event_carries_phase_and_pair_id():
    manifest = _manifest()
    manifest["frames"][0]["phase"] = "before"
    manifest["frames"][0]["pair_id"] = "conv-otlp-1/pair/1"
    (event,) = _spans(otlp.manifest_to_otlp(manifest))[1]["events"]
    event_attrs = _attrs(event["attributes"])
    assert event_attrs["shotquill.frame.phase"] == "before"
    assert event_attrs["shotquill.frame.pair_id"] == "conv-otlp-1/pair/1"


def test_ids_are_valid_hex_and_deterministic():
    first = otlp.manifest_to_otlp(_manifest())
    second = otlp.manifest_to_otlp(_manifest())
    root = _spans(first)[0]
    assert len(root["traceId"]) == 32 and int(root["traceId"], 16) >= 0
    assert len(root["spanId"]) == 16 and int(root["spanId"], 16) >= 0
    # Same manifest -> same ids (a hash, not a random draw).
    assert first == second


def test_timestamps_are_unix_nano_strings():
    document = otlp.manifest_to_otlp(_manifest())
    root, frame = _spans(document)
    start, end = int(root["startTimeUnixNano"]), int(root["endTimeUnixNano"])
    # Int64 nanos carried as strings; second-resolution input -> exact integers.
    assert root["startTimeUnixNano"].isdigit()
    assert end - start == 5 * 1_000_000_000  # started_at .. ended_at is 5 s
    assert int(frame["startTimeUnixNano"]) == start + 3 * 1_000_000_000  # frame at +3 s
    assert frame["startTimeUnixNano"] == frame["endTimeUnixNano"]  # instantaneous capture


def test_resource_carries_service_and_semconv_version():
    document = otlp.manifest_to_otlp(_manifest(), service_version="9.9.9")
    resource_attrs = _attrs(document["resourceSpans"][0]["resource"]["attributes"])
    assert resource_attrs["service.name"] == "shotquill"
    assert resource_attrs["service.version"] == "9.9.9"
    assert resource_attrs["shotquill.genai.semconv_version"] == otlp.SEMCONV_VERSION


def test_empty_session_renders_just_the_root_span():
    manifest = _manifest()
    manifest["frames"] = []
    spans = _spans(otlp.manifest_to_otlp(manifest))
    assert len(spans) == 1
    assert spans[0]["name"] == "invoke_agent builder"


def test_missing_timestamp_maps_to_zero():
    manifest = _manifest()
    manifest["ended_at"] = None
    manifest["started_at"] = None
    spans = _spans(otlp.manifest_to_otlp(manifest))
    assert spans[0]["startTimeUnixNano"] == "0"
