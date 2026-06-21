# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Flight-recorder tests: the session format (pure) and the CLI round trip.

The format logic in :mod:`shotquill.record` owns no pixels, so most of this
file feeds it plain bytes and asserts on the manifest / filmstrip without a
screen. The CLI section drives ``record start|frame|end`` against an in-memory
FakeCapturer, mirroring test_cli.py.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from shotquill import audit, cli, headless, paths, record
from shotquill.capture.base import CaptureResult, DisplayInfo, Rect, WindowInfo

_FAKE_PNG = b"\x89PNG\r\n\x1a\nfake-bytes"
_FIXED = dt.datetime(2026, 6, 13, 10, 0, 3).astimezone()


# --- session format (pure, no Qt) -------------------------------------------


def test_start_writes_manifest_with_otel_fields(tmp_path):
    session = record.start_session(
        records_root=tmp_path,
        session_id="conv-test-1",
        agent_name="builder",
        agent_id="agent-7",
        label="login flow",
        now=_FIXED,
    )
    assert session.dir == tmp_path / "conv-test-1"
    assert session.frames_dir.is_dir()
    manifest = json.loads(session.manifest_path.read_text(encoding="utf-8"))
    assert manifest["shotquill_manifest_version"] == record.MANIFEST_VERSION
    assert manifest["conversation_id"] == "conv-test-1"
    assert manifest["agent"] == {"name": "builder", "id": "agent-7"}
    assert manifest["status"] == record.STATUS_RECORDING
    assert manifest["label"] == "login flow"
    assert manifest["started_at"].startswith("2026-06-13T10:00:03")
    assert manifest["ended_at"] is None
    assert manifest["frames"] == []


def test_start_pins_explicit_directory(tmp_path):
    target = tmp_path / "ci-artifacts" / "run-42"
    session = record.start_session(directory=target, session_id="conv-x", now=_FIXED)
    assert session.dir == target
    assert session.manifest_path.is_file()


def test_new_session_id_format():
    sid = record.new_session_id(_FIXED, suffix="abc123")
    assert sid == "conv-20260613-100003-abc123"


@pytest.mark.parametrize(
    "bad",
    ["../escape", "a/b", "..", ".", ".hidden", "x\\y", "foo/../bar", "/abs"],
)
def test_start_rejects_path_traversal_ids(tmp_path, bad):
    # A caller-supplied id must be an inert name, never a path: ../x would file
    # the session outside the records root (and later smuggle a Zip-Slip entry).
    with pytest.raises(record.RecordError, match="invalid session id"):
        record.start_session(records_root=tmp_path, session_id=bad)
    assert list(tmp_path.iterdir()) == []  # nothing created on the way out


def test_start_accepts_ordinary_ids(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-OK_1.2-3")
    assert session.dir == tmp_path / "conv-OK_1.2-3"


def test_record_frame_appends_and_writes_image(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-1", now=_FIXED)
    frame = record.record_frame(
        session,
        image_bytes=_FAKE_PNG,
        tool="click",
        target="window 33",
        label="click submit",
        redacted=True,
        now=_FIXED,
    )
    assert frame.index == 1
    assert frame.image == "frames/0001.png"
    assert (session.dir / frame.image).read_bytes() == _FAKE_PNG

    manifest = record.load_manifest(session)
    assert len(manifest["frames"]) == 1
    entry = manifest["frames"][0]
    assert entry["span"]["tool_name"] == "click"
    assert entry["span"]["tool_call_id"] == "conv-1/frame/1"
    assert entry["label"] == "click submit"
    assert entry["target"] == "window 33"
    assert entry["redacted"] is True


def test_record_frame_increments_index(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-2", now=_FIXED)
    record.record_frame(session, image_bytes=_FAKE_PNG, tool="a", target="t")
    second = record.record_frame(session, image_bytes=_FAKE_PNG, tool="b", target="t")
    assert second.index == 2
    assert second.image == "frames/0002.png"
    assert len(record.load_manifest(session)["frames"]) == 2


def test_frame_after_end_is_refused(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-3", now=_FIXED)
    record.end_session(session, now=_FIXED)
    with pytest.raises(record.RecordError, match="already closed"):
        record.record_frame(session, image_bytes=_FAKE_PNG, tool="a", target="t")


def test_end_marks_complete_and_renders_filmstrip(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-4", now=_FIXED)
    record.record_frame(session, image_bytes=_FAKE_PNG, tool="open", target="fullscreen")
    html_path = record.end_session(session, now=_FIXED)

    assert html_path == session.filmstrip_path
    manifest = record.load_manifest(session)
    assert manifest["status"] == record.STATUS_COMPLETE
    assert manifest["ended_at"].startswith("2026-06-13T10:00:03")
    html = html_path.read_text(encoding="utf-8")
    assert "frames/0001.png" in html
    assert "1 frame(s)" in html

    # end also drops the OTLP/JSON projection next to the filmstrip.
    otlp_doc = json.loads(session.otlp_path.read_text(encoding="utf-8"))
    spans = otlp_doc["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert [s["name"] for s in spans] == ["invoke_agent", "execute_tool open"]


def test_resolve_by_id_and_by_path(tmp_path):
    record.start_session(records_root=tmp_path, session_id="conv-5", now=_FIXED)
    by_id = record.resolve_session("conv-5", records_root=tmp_path)
    by_path = record.resolve_session(str(tmp_path / "conv-5"))
    assert by_id.id == by_path.id == "conv-5"


def test_resolve_missing_session_raises(tmp_path):
    with pytest.raises(record.SessionNotFound):
        record.resolve_session("nope", records_root=tmp_path)


def test_corrupt_manifest_raises(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-6", now=_FIXED)
    session.manifest_path.write_text("{not json", encoding="utf-8")
    with pytest.raises(record.RecordError, match="corrupt"):
        record.load_manifest(session)


def test_unknown_manifest_version_raises(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-7", now=_FIXED)
    data = json.loads(session.manifest_path.read_text(encoding="utf-8"))
    data["shotquill_manifest_version"] = 999
    session.manifest_path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(record.RecordError, match="version"):
        record.load_manifest(session)


# --- filmstrip rendering ----------------------------------------------------


def test_filmstrip_escapes_app_supplied_strings():
    manifest = {
        "shotquill_manifest_version": 1,
        "conversation_id": "conv-x",
        "agent": {"name": None, "id": None},
        "status": "complete",
        "started_at": "2026-06-13T10:00:00",
        "ended_at": "2026-06-13T10:01:00",
        "frames": [
            {
                "span": {"tool_name": "click", "tool_call_id": "conv-x/frame/1"},
                "at": "2026-06-13T10:00:03",
                "label": "<script>alert(1)</script>",
                "image": "frames/0001.png",
                "target": "window <b>Mail</b>",
                "redacted": True,
            }
        ],
    }
    html = record.render_filmstrip(manifest)
    # The injected markup is escaped, not live.
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "window &lt;b&gt;Mail&lt;/b&gt;" in html
    assert "redacted" in html  # the badge renders for a redacted frame


def test_filmstrip_handles_empty_session():
    manifest = {
        "shotquill_manifest_version": 1,
        "conversation_id": "conv-empty",
        "agent": {"name": None, "id": None},
        "status": "complete",
        "started_at": "2026-06-13T10:00:00",
        "ended_at": None,
        "frames": [],
    }
    html = record.render_filmstrip(manifest)
    assert "No frames recorded." in html


# --- before/after pairing (pure) --------------------------------------------


def test_before_opens_a_pair_and_after_joins_it(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-pair")
    before = record.record_frame(
        session, image_bytes=_FAKE_PNG, tool="click", target="t", phase="before"
    )
    after = record.record_frame(
        session, image_bytes=b"different", tool="click", target="t", phase="after"
    )
    assert before.phase == "before" and after.phase == "after"
    assert before.pair_id == after.pair_id  # two halves, one pair
    frames = record.load_manifest(session)["frames"]
    assert [f.get("phase") for f in frames] == ["before", "after"]
    assert frames[0]["pair_id"] == frames[1]["pair_id"]


def test_lone_after_without_a_before_raises(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-lone")
    with pytest.raises(record.RecordError, match="no open '--before'"):
        record.record_frame(session, image_bytes=_FAKE_PNG, tool="x", target="t", phase="after")


def test_pairs_nest_like_brackets(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-nest")
    outer_b = record.record_frame(session, image_bytes=b"1", tool="t", target="x", phase="before")
    inner_b = record.record_frame(session, image_bytes=b"2", tool="t", target="x", phase="before")
    inner_a = record.record_frame(session, image_bytes=b"3", tool="t", target="x", phase="after")
    outer_a = record.record_frame(session, image_bytes=b"4", tool="t", target="x", phase="after")
    # The first 'after' closes the most recent open 'before' (inner), the next the outer.
    assert inner_a.pair_id == inner_b.pair_id
    assert outer_a.pair_id == outer_b.pair_id
    assert inner_b.pair_id != outer_b.pair_id


def test_open_before_pair_id_is_pure_over_frames():
    frames = [
        {"phase": "before", "pair_id": "p1"},
        {"phase": "after"},
        {"phase": "before", "pair_id": "p2"},
    ]
    assert record.open_before_pair_id(frames) == "p2"  # p1 closed, p2 still open
    assert record.open_before_pair_id([]) is None


def test_unpaired_frame_has_no_phase_field(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-plain")
    record.record_frame(session, image_bytes=_FAKE_PNG, tool="click", target="t")
    entry = record.load_manifest(session)["frames"][0]
    assert "phase" not in entry and "pair_id" not in entry


def test_filmstrip_renders_phase_badge():
    manifest = {
        "shotquill_manifest_version": 1,
        "conversation_id": "conv-ph",
        "agent": {"name": None, "id": None},
        "status": "complete",
        "started_at": "2026-06-13T10:00:00",
        "ended_at": None,
        "frames": [
            {
                "span": {"tool_name": "click", "tool_call_id": "conv-ph/frame/1"},
                "at": "2026-06-13T10:00:03",
                "label": None,
                "image": "frames/0001.png",
                "target": "fullscreen",
                "redacted": False,
                "phase": "before",
                "pair_id": "conv-ph/pair/1",
            }
        ],
    }
    html = record.render_filmstrip(manifest)
    assert '<span class="badge phase">before</span>' in html


def _frame(idx, *, phase=None, pair_id=None, kind="action", tool="click"):
    entry = {
        "span": {"tool_name": tool, "tool_call_id": f"conv/frame/{idx}"},
        "at": "2026-06-13T10:00:00",
        "label": None,
        "image": f"frames/{idx:04d}.png",
        "target": "fullscreen",
        "redacted": False,
        "kind": kind,
    }
    if phase:
        entry["phase"] = phase
    if pair_id:
        entry["pair_id"] = pair_id
    return entry


def _manifest(frames):
    return {
        "shotquill_manifest_version": 1,
        "conversation_id": "conv-d",
        "agent": {"name": None, "id": None},
        "status": "complete",
        "started_at": "2026-06-13T10:00:00",
        "ended_at": None,
        "frames": frames,
    }


def test_filmstrip_groups_before_after_into_one_pair_block():
    html = record.render_filmstrip(
        _manifest(
            [
                _frame(1, phase="before", pair_id="conv-d/pair/1"),
                _frame(2, phase="after", pair_id="conv-d/pair/1"),
            ]
        )
    )
    assert html.count('<div class="pair">') == 1  # the two halves share one block
    assert html.count("<figure") == 2
    assert 'badge phase">before' in html and 'badge phase">after' in html


def test_filmstrip_pulls_after_next_to_before_past_an_observation():
    # before → (observation captured between) → after: the after is pulled up
    # beside its before, so an observation taken in between sorts *after* the pair
    # block rather than splitting it.
    html = record.render_filmstrip(
        _manifest(
            [
                _frame(1, phase="before", pair_id="conv-d/pair/1"),
                _frame(2, kind="observation", tool="capture"),
                _frame(3, phase="after", pair_id="conv-d/pair/1"),
            ]
        )
    )
    assert html.count('<div class="pair">') == 1
    assert html.count("<figure") == 3  # observation still rendered, just standalone
    before, after, obs = (html.index(f"frames/{i:04d}.png") for i in (1, 3, 2))
    assert before < after < obs  # before+after grouped first, observation trails


def test_filmstrip_unpaired_frames_have_no_pair_block():
    html = record.render_filmstrip(_manifest([_frame(1)]))
    assert '<div class="pair">' not in html
    assert html.count("<figure") == 1


def test_filmstrip_overlays_a_diff_box_when_present():
    after = _frame(2, phase="after", pair_id="conv-d/pair/1")
    after["diff"] = {"x": 0.5, "y": 0.25, "width": 0.4, "height": 0.3}
    html = record.render_filmstrip(
        _manifest([_frame(1, phase="before", pair_id="conv-d/pair/1"), after])
    )
    assert 'class="diffbox"' in html
    assert "left:50.00%" in html and "top:25.00%" in html
    assert "width:40.00%" in html and "height:30.00%" in html


def test_filmstrip_clamps_out_of_range_diff_fractions():
    after = _frame(2, phase="after", pair_id="p")
    after["diff"] = {"x": -1, "y": 2, "width": "junk", "height": 0.5}
    html = record.render_filmstrip(_manifest([_frame(1, phase="before", pair_id="p"), after]))
    assert "left:0.00%" in html and "top:100.00%" in html  # clamped to [0,1]
    assert "width:0.00%" in html  # non-numeric → 0


# --- before/after change boxes (attach_diffs pure; annotate uses Qt) ----------


def test_attach_diffs_sets_diff_by_position(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-ad", now=_FIXED)
    record.record_frame(session, image_bytes=_FAKE_PNG, tool="t", target="x")
    record.record_frame(session, image_bytes=b"second", tool="t", target="x")
    record.attach_diffs(session, {1: {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}})
    frames = record.load_manifest(session)["frames"]
    assert "diff" not in frames[0]
    assert frames[1]["diff"] == {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}


def test_attach_diffs_empty_is_a_noop(tmp_path):
    session = record.start_session(records_root=tmp_path, session_id="conv-ad2", now=_FIXED)
    record.record_frame(session, image_bytes=_FAKE_PNG, tool="t", target="x")
    record.attach_diffs(session, {})
    assert "diff" not in record.load_manifest(session)["frames"][0]


def _png_bytes(image) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    image.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def test_annotate_pair_diffs_records_the_change_box(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtGui import QColor, QImage

    session = record.start_session(records_root=tmp_path, session_id="conv-anno", now=_FIXED)
    before = QImage(40, 20, QImage.Format.Format_RGBA8888)
    before.fill(QColor(0, 0, 0))
    after = QImage(40, 20, QImage.Format.Format_RGBA8888)
    after.fill(QColor(0, 0, 0))
    for x in range(20, 40):  # change the bottom-right quadrant
        for y in range(10, 20):
            after.setPixelColor(x, y, QColor(255, 255, 255))

    record.record_frame(
        session, image_bytes=_png_bytes(before), tool="click", target="x", phase="before"
    )
    record.record_frame(
        session, image_bytes=_png_bytes(after), tool="click", target="x", phase="after"
    )
    headless.annotate_pair_diffs(session)

    frames = record.load_manifest(session)["frames"]
    assert "diff" not in frames[0]  # the before frame carries no box
    box = frames[1]["diff"]
    assert box["x"] > 0.4 and box["y"] > 0.4  # change is in the bottom-right


def test_annotate_pair_diffs_no_box_when_frames_identical(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtGui import QColor, QImage

    session = record.start_session(records_root=tmp_path, session_id="conv-anno2", now=_FIXED)
    img = QImage(20, 20, QImage.Format.Format_RGBA8888)
    img.fill(QColor(10, 10, 10))
    record.record_frame(
        session, image_bytes=_png_bytes(img), tool="click", target="x", phase="before"
    )
    record.record_frame(
        session, image_bytes=_png_bytes(img), tool="click", target="x", phase="after"
    )
    headless.annotate_pair_diffs(session)
    assert "diff" not in record.load_manifest(session)["frames"][1]


# --- CLI round trip ---------------------------------------------------------


def _result(width: int = 2, height: int = 2) -> CaptureResult:
    return CaptureResult(
        width=width, height=height, scale=1.0, pixels=bytes([255, 0, 0, 255] * width * height)
    )


class FakeCapturer:
    def __init__(self) -> None:
        self.include_cursor = False
        self.windows = [
            WindowInfo(window_id=33, owner="Notes", title="Scratch", bounds=Rect(5, 5, 300, 200)),
        ]
        self.displays = [
            DisplayInfo(index=0, name="built-in", bounds=Rect(0, 0, 1440, 900), primary=True),
        ]

    def capture_fullscreen(self, exclude_window_ids=frozenset()) -> CaptureResult:
        return _result()

    def capture_region(self, region: Rect) -> CaptureResult:
        return _result()

    def capture_window(self, window_id: int) -> CaptureResult:
        return _result()

    def list_windows(self) -> list[WindowInfo]:
        return self.windows

    def list_displays(self) -> list[DisplayInfo]:
        return self.displays


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """Keep records, audit, and capture temp inside tmp and off syslog."""
    monkeypatch.setattr(paths, "records_dir", lambda: tmp_path / "records")
    monkeypatch.setattr(paths, "audit_log_path", lambda: tmp_path / "audit.log")
    monkeypatch.setattr(audit, "_to_system_log", lambda line: None)
    monkeypatch.setattr(audit, "_caller_chain", lambda: ["pytest"])


@pytest.fixture
def fake_capturer(monkeypatch):
    pytest.importorskip("PySide6")
    capturer = FakeCapturer()
    monkeypatch.setattr(headless, "get_capturer", lambda include_cursor=False: capturer)
    return capturer


def _empty_blocklist(monkeypatch):
    from shotquill import blocklist as bl

    monkeypatch.setattr(headless, "active_blocklist", lambda: bl.Blocklist(()))


def test_cli_round_trip(fake_capturer, monkeypatch, capsys, tmp_path):
    _empty_blocklist(monkeypatch)

    assert cli.main(["session", "start", "--id", "conv-cli", "--agent", "builder"]) == 0
    session_dir = capsys.readouterr().out.strip()
    assert session_dir.endswith("conv-cli")

    assert cli.main(["session", "frame", session_dir, "--tool", "click", "--label", "submit"]) == 0
    image_path = capsys.readouterr().out.strip()
    # The returned path is absolute and uses native separators (backslashes on
    # Windows); the stored relative field stays forward-slashed.
    assert image_path.replace("\\", "/").endswith("frames/0001.png")
    with open(image_path, "rb") as fh:
        assert fh.read(4) == b"\x89PNG"

    assert cli.main(["session", "end", session_dir]) == 0
    html_path = capsys.readouterr().out.strip()
    assert html_path.endswith("index.html")

    manifest = json.loads((tmp_path / "records" / "conv-cli" / "manifest.json").read_text())
    assert manifest["status"] == "complete"
    assert len(manifest["frames"]) == 1
    assert manifest["frames"][0]["redacted"] is False  # empty blocklist


def test_cli_before_after_pairs_frames(fake_capturer, monkeypatch, capsys, tmp_path):
    _empty_blocklist(monkeypatch)
    cli.main(["session", "start", "--id", "conv-ba"])
    capsys.readouterr()

    assert cli.main(["session", "frame", "conv-ba", "--tool", "click", "--before", "--json"]) == 0
    before = json.loads(capsys.readouterr().out)
    assert cli.main(["session", "frame", "conv-ba", "--tool", "click", "--after", "--json"]) == 0
    after = json.loads(capsys.readouterr().out)
    assert before["phase"] == "before" and after["phase"] == "after"
    assert before["pair_id"] == after["pair_id"]


def test_cli_lone_after_is_an_error(fake_capturer, monkeypatch, capsys):
    _empty_blocklist(monkeypatch)
    cli.main(["session", "start", "--id", "conv-lone-cli"])
    capsys.readouterr()
    rc = cli.main(["session", "frame", "conv-lone-cli", "--tool", "click", "--after"])
    assert rc == 1
    assert "no open '--before'" in capsys.readouterr().err


def test_cli_before_and_after_are_mutually_exclusive(fake_capturer, monkeypatch, capsys):
    _empty_blocklist(monkeypatch)
    cli.main(["session", "start", "--id", "conv-excl"])
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc:
        cli.main(["session", "frame", "conv-excl", "--tool", "click", "--before", "--after"])
    assert exc.value.code == 2  # argparse usage error


def test_cli_frame_resolves_id_against_default_root(fake_capturer, monkeypatch, capsys):
    _empty_blocklist(monkeypatch)
    cli.main(["session", "start", "--id", "conv-byid"])
    capsys.readouterr()
    # Thread the bare id (not the path) back; it resolves under records_dir().
    assert cli.main(["session", "frame", "conv-byid", "--tool", "type"]) == 0


def test_cli_frame_redacted_flag_tracks_blocklist(fake_capturer, monkeypatch, capsys):
    from shotquill import blocklist as bl

    monkeypatch.setattr(
        headless, "active_blocklist", lambda: bl.Blocklist((bl.BlockRule(name="1Password"),))
    )
    cli.main(["session", "start", "--id", "conv-redact"])
    session_dir = capsys.readouterr().out.strip()
    assert cli.main(["session", "frame", session_dir, "--tool", "click", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["redacted"] is True


def test_cli_frame_missing_session_is_error(fake_capturer, monkeypatch, capsys):
    _empty_blocklist(monkeypatch)
    assert cli.main(["session", "frame", "ghost", "--tool", "click"]) == 1
    assert "no recording session" in capsys.readouterr().err


def test_cli_frame_dedup_references_previous(fake_capturer, monkeypatch, capsys, tmp_path):
    # The FakeCapturer returns identical pixels each call; deterministic encoding
    # makes them byte-identical, so --dedup files the second frame as a reference.
    _empty_blocklist(monkeypatch)
    cli.main(["session", "start", "--id", "conv-dedup"])
    session_dir = capsys.readouterr().out.strip()
    cli.main(["session", "frame", session_dir, "--tool", "a", "--dedup"])
    cli.main(["session", "frame", session_dir, "--tool", "b", "--dedup"])
    capsys.readouterr()

    frames_dir = tmp_path / "records" / "conv-dedup" / "frames"
    assert (frames_dir / "0001.png").exists()
    assert not (frames_dir / "0002.png").exists()  # second deduped to the first
    manifest = json.loads((tmp_path / "records" / "conv-dedup" / "manifest.json").read_text())
    assert manifest["frames"][1]["deduped"] is True


def test_cli_frame_max_dimension_shrinks_the_stored_image(fake_capturer, monkeypatch, capsys):
    from PySide6.QtGui import QImage

    _empty_blocklist(monkeypatch)
    cli.main(["session", "start", "--id", "conv-small"])
    session_dir = capsys.readouterr().out.strip()
    # FakeCapturer yields a 2x2 frame; cap the long edge to 1 -> a 1x1 archive.
    cli.main(["session", "frame", session_dir, "--tool", "a", "--max-dimension", "1"])
    image_path = capsys.readouterr().out.strip()
    stored = QImage(image_path)
    assert (stored.width(), stored.height()) == (1, 1)


def test_cli_record_audits_via_record(fake_capturer, monkeypatch, capsys, tmp_path):
    _empty_blocklist(monkeypatch)
    cli.main(["session", "start", "--id", "conv-audit"])
    session_dir = capsys.readouterr().out.strip()
    cli.main(["session", "frame", session_dir, "--tool", "click"])
    cli.main(["session", "end", session_dir])
    capsys.readouterr()

    entries = [
        json.loads(line)
        for line in (tmp_path / "audit.log").read_text(encoding="utf-8").splitlines()
    ]
    actions = {e["action"] for e in entries}
    assert {"record_start", "record_frame", "record_end"} <= actions
    assert all(e["via"] == "record" for e in entries)
