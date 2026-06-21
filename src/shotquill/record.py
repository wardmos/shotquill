# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Agent flight-recorder sessions: a trace of frames, not a single capture.

Where ``squill capture`` returns one image, ``squill session`` accumulates a
*session* — an ordered run of frames an agent leaves behind as it operates a
real screen, so a human or a reviewing AI can see what it did, step by step.

This module is the session store and its on-disk format; it owns no pixels and
imports no Qt. The CLI captures and redacts a frame, then hands the already
encoded bytes here to be filed under the session. Splitting it this way keeps
the format unit-testable without a screen, mirroring :mod:`shotquill.redact`
and :mod:`shotquill.deterministic`.

Format. Each session is a directory::

    <records>/<session-id>/
        manifest.json     the trace (one object; the source of truth)
        frames/0001.png   one file per recorded frame
        index.html        a static filmstrip, written at `record end`

The manifest is a *local projection* of an OpenTelemetry GenAI trace: a session
is an ``invoke_agent`` span (``gen_ai.conversation.id`` == our session id), each
frame an ``execute_tool`` span carrying the screenshot as a ``shotquill.frame.*``
event. We do not depend on the OTel SDK yet — the field names are chosen so an
OTLP exporter can be bolted on later without reshaping what is already on disk.

Privacy. The record path keeps the blocklist redaction default-on (the CLI does
not expose a way to turn it off mid-trace), so a blocked app cannot be filed
into an archive by an agent that "forgot" to mask it. The honest limit still
holds: redaction only covers *known* apps, so the manifest's ``redacted`` flag
means "blocklist protection was in force for this frame", not "this frame is
free of user content" — agent actions and user pixels are the same pixels.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import tarfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

MANIFEST_NAME = "manifest.json"
FILMSTRIP_NAME = "index.html"
FRAMES_SUBDIR = "frames"

# Bumped only on a breaking change to the on-disk shape, so a reader can refuse
# a manifest it does not understand rather than mis-parse a future one.
MANIFEST_VERSION = 1

STATUS_RECORDING = "recording"
STATUS_COMPLETE = "complete"

# Frame kinds. An "action" frame documents a deliberate step (explicit
# `record frame`, with a tool + label); an "observation" frame is one mirrored
# passively from a `capture` the agent did to *see* the screen while a session
# was active (no label, not a tool call). Keeping them distinct stops a passive
# glance from masquerading as an action in the timeline or the trace.
KIND_ACTION = "action"
KIND_OBSERVATION = "observation"

# A frame can optionally be one half of a before/after pair around a single
# action, so a reviewer can diff "what changed when the agent did X". The two
# frames share a ``pair_id``; ``phase`` says which side this frame is.
PHASE_BEFORE = "before"
PHASE_AFTER = "after"


class RecordError(Exception):
    """A flight-recorder operation failed (bad/missing session, corrupt manifest)."""


class SessionNotFound(RecordError):
    """No session directory at the given handle (id or path)."""


@dataclass(frozen=True)
class FrameRecord:
    """One recorded frame — the data the manifest keeps per ``execute_tool`` span."""

    index: int  # 1-based order within the session
    at: str  # ISO-8601 capture time
    tool: str  # gen_ai.tool.name — the action this frame documents
    label: str | None  # shotquill.frame.label — human-readable note
    image: str  # shotquill.frame.image_ref — path relative to the session dir
    target: str  # what was actually captured (window / region / fullscreen)
    redacted: bool  # shotquill.frame.redacted — blocklist protection was in force
    kind: str = KIND_ACTION  # action (deliberate step) vs observation (passive)
    # None when the frame carried no assertion; otherwise whether every OCR
    # check on it held — this is what makes a failed test a frame in the trace.
    assertion_passed: bool | None = None
    # before/after pairing: ``phase`` is "before"/"after" (or None for a lone
    # frame); ``pair_id`` links the two halves of one action.
    phase: str | None = None
    pair_id: str | None = None

    def as_manifest_entry(self, *, tool_call_id: str) -> dict:
        """Serialize with OTel-derived field names (see module docstring)."""
        entry = {
            "span": {
                "tool_name": self.tool,
                "tool_call_id": tool_call_id,  # gen_ai.tool.call.id
            },
            "at": self.at,
            "kind": self.kind,
            "label": self.label,
            "image": self.image,
            "target": self.target,
            "redacted": self.redacted,
        }
        if self.assertion_passed is not None:
            entry["assertion_passed"] = self.assertion_passed
        if self.phase is not None:
            entry["phase"] = self.phase
        if self.pair_id is not None:
            entry["pair_id"] = self.pair_id
        return entry


def now_iso(now: dt.datetime | None = None) -> str:
    """Local-aware ISO timestamp, matching the audit log's format."""
    moment = now or dt.datetime.now().astimezone()
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.isoformat(timespec="seconds")


def new_session_id(now: dt.datetime | None = None, *, suffix: str | None = None) -> str:
    """A readable, collision-resistant conversation id (``conv-<date>-<rand>``).

    The date prefix sorts and skims well in a directory listing; the random
    suffix keeps two sessions started in the same second apart.
    """
    moment = now or dt.datetime.now().astimezone()
    tail = suffix if suffix is not None else uuid.uuid4().hex[:6]
    return f"conv-{moment:%Y%m%d-%H%M%S}-{tail}"


# A caller-supplied session id becomes a path segment (``<records>/<id>/``) and
# the top-level folder of an export archive, so it must be an inert *name*, never
# a path. The blocklist threat model includes an injected agent driving the CLI/
# MCP, and ``record start --id ../x`` would otherwise file a session outside the
# records root or smuggle a ``..`` entry into a zip/tar (Zip/Tar Slip). Allow
# only a conservative charset, no leading dot (forbids ``.``/``..``/hidden); the
# generated ids (``conv-<date>-<hex>``) already fit. ``--dir`` stays the escape
# hatch for pinning an exact location on purpose.
_SESSION_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def validate_session_id(session_id: str) -> str:
    """Return ``session_id`` unchanged if it is a safe path segment, else raise.

    Safe means a non-empty run of ``[A-Za-z0-9._-]`` not starting with a dot, so
    it can never traverse out of the records root or add a ``..`` entry to an
    export archive.
    """
    if not _SESSION_ID_RE.match(session_id):
        raise RecordError(f"invalid session id {session_id!r}: use only letters, digits, . _ -")
    return session_id


@dataclass(frozen=True)
class Session:
    """A live handle to a session directory (the manifest is the durable truth)."""

    id: str
    dir: Path

    @property
    def manifest_path(self) -> Path:
        return self.dir / MANIFEST_NAME

    @property
    def frames_dir(self) -> Path:
        return self.dir / FRAMES_SUBDIR

    @property
    def filmstrip_path(self) -> Path:
        return self.dir / FILMSTRIP_NAME

    @property
    def otlp_path(self) -> Path:
        from shotquill import otlp

        return self.dir / otlp.OTLP_NAME


def start_session(
    *,
    session_id: str | None = None,
    directory: Path | None = None,
    records_root: Path | None = None,
    agent_name: str | None = None,
    agent_id: str | None = None,
    label: str | None = None,
    now: dt.datetime | None = None,
) -> Session:
    """Create a new session directory and write its initial manifest.

    By default the session lands at ``<records_root>/<session-id>/``. Pass
    ``directory`` to pin an exact location (e.g. a CI artifact path); that
    directory then *is* the handle later commands resolve.
    """
    sid = validate_session_id(session_id) if session_id else new_session_id(now)
    if directory is not None:
        session_dir = Path(directory).expanduser()
    else:
        root = records_root if records_root is not None else _default_records_root()
        session_dir = root / sid
    # ``0o700``: a frame archive is at least as sensitive as the audit log —
    # other local users have no business reading what an agent captured.
    session_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    (session_dir / FRAMES_SUBDIR).mkdir(mode=0o700, exist_ok=True)

    manifest = {
        "shotquill_manifest_version": MANIFEST_VERSION,
        "conversation_id": sid,  # gen_ai.conversation.id
        "agent": {"name": agent_name, "id": agent_id},  # gen_ai.agent.name / .id
        "status": STATUS_RECORDING,
        "label": label,
        "started_at": now_iso(now),
        "ended_at": None,
        "frames": [],
    }
    session = Session(id=sid, dir=session_dir)
    _write_manifest(session.manifest_path, manifest)
    return session


def resolve_session(handle: str, *, records_root: Path | None = None) -> Session:
    """Turn a ``--session`` handle into a :class:`Session`.

    A handle that names an existing directory (or looks like a path) is used
    as-is, so a caller who pinned ``--dir`` threads that path straight back. A
    bare id resolves under the default records root. Either way the manifest
    must exist, or the session is treated as not found.
    """
    candidate = Path(handle).expanduser()
    looks_like_path = os.sep in handle or (os.altsep and os.altsep in handle) or candidate.is_dir()
    if looks_like_path:
        session_dir = candidate
    else:
        root = records_root if records_root is not None else _default_records_root()
        session_dir = root / handle
    manifest_path = session_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise SessionNotFound(f"no recording session at {handle!r} (expected {manifest_path})")
    manifest = _read_manifest(manifest_path)
    return Session(id=manifest.get("conversation_id", session_dir.name), dir=session_dir)


def load_manifest(session: Session) -> dict:
    """Read a session's manifest (the trace), validating its version."""
    return _read_manifest(session.manifest_path)


def open_before_pair_id(frames: list[dict]) -> str | None:
    """The ``pair_id`` of the most recent ``before`` frame still awaiting its ``after``.

    A trace is one agent's linear run, so before/after pairs nest like brackets:
    each ``before`` opens a pair and the next ``after`` closes the most recent
    open one. Returns ``None`` when nothing is open (an ``after`` with no matching
    ``before``). Pure — it reads only the manifest's frame list.
    """
    open_pairs: list[str] = []
    for frame in frames:
        phase = frame.get("phase")
        if phase == PHASE_BEFORE and frame.get("pair_id"):
            open_pairs.append(frame["pair_id"])
        elif phase == PHASE_AFTER and open_pairs:
            open_pairs.pop()
    return open_pairs[-1] if open_pairs else None


def attach_diffs(session: Session, diffs: dict[int, dict]) -> None:
    """Store before/after change boxes on frames, keyed by 0-based position (pure).

    ``diffs`` maps a frame's position in the manifest to a ``{x, y, width, height}``
    box in frame fractions (computed with Qt by the record-end glue, see
    :func:`shotquill.headless.compute_pair_diffs`) — this module stays image-free.
    A no-op when ``diffs`` is empty.
    """
    if not diffs:
        return
    manifest = _read_manifest(session.manifest_path)
    frames = manifest["frames"]
    for pos, box in diffs.items():
        if 0 <= pos < len(frames):
            frames[pos]["diff"] = dict(box)
    _write_manifest(session.manifest_path, manifest)


def record_frame(
    session: Session,
    *,
    image_bytes: bytes,
    tool: str,
    target: str,
    label: str | None = None,
    redacted: bool = False,
    kind: str = KIND_ACTION,
    assertions: list[dict] | None = None,
    pii: list[dict] | None = None,
    phase: str | None = None,
    image_ext: str = "png",
    dedup: bool = False,
    now: dt.datetime | None = None,
) -> FrameRecord:
    """File one already-encoded, already-redacted frame under the session.

    Appends to the manifest and writes the image; returns the frame record. The
    caller (the CLI) owns the capture + redaction so this module stays Qt-free.
    ``kind`` is ``"action"`` for a deliberate step or ``"observation"`` for a
    passively mirrored capture. ``assertions`` is an optional list of
    already-evaluated OCR checks (each a ``{"kind", "pattern", "passed"}`` dict);
    when given, the frame records whether they all held, so a failed test becomes
    a frame in the trace. ``pii`` is an optional list of best-effort PII findings
    (each a ``{"kind", "count"}`` dict from :mod:`pii`) — kind and count only,
    never the value — recorded as a residual-risk flag on the frame. ``phase`` is
    ``"before"`` / ``"after"`` to file this frame as one half of a before/after
    pair around an action (an ``"after"`` joins the most recent open ``"before"``;
    a lone ``"after"`` raises). Not safe to call concurrently for one session — a
    trace is one agent's linear run, and the next index is read from the manifest
    on each call.

    ``dedup`` drops the cost of an unchanged screen: when the new bytes are
    identical to the previous frame's image, the new frame *references* that same
    file instead of writing a duplicate (and is flagged ``deduped`` in the
    manifest). It still gets its own frame entry — it is a distinct step in the
    timeline, it just shares pixels. Byte-identity is reliable when frames are
    encoded deterministically (the CLI record path does); without that, an
    unchanged screen may still differ byte-wise and simply won't be deduped.
    """
    if phase not in (None, PHASE_BEFORE, PHASE_AFTER):
        raise RecordError(f"phase must be {PHASE_BEFORE!r} or {PHASE_AFTER!r}, got {phase!r}")
    manifest = _load_open_manifest(session)
    index = len(manifest["frames"]) + 1
    # Resolve before/after pairing: a 'before' opens a new pair, an 'after' joins
    # the most recent still-open one (a lone 'after' is the caller's mistake).
    pair_id: str | None = None
    if phase == PHASE_BEFORE:
        pair_id = f"{session.id}/pair/{index}"
    elif phase == PHASE_AFTER:
        pair_id = open_before_pair_id(manifest["frames"])
        if pair_id is None:
            raise RecordError("no open '--before' frame to pair this '--after' with")
    digest = hashlib.sha256(image_bytes).hexdigest()
    rel_image = f"{FRAMES_SUBDIR}/{index:04d}.{image_ext}"
    deduped = False
    if dedup and manifest["frames"]:
        previous = manifest["frames"][-1]
        if previous.get("image_sha256") == digest:
            # Same pixels as the last frame: point at its file, write nothing.
            rel_image = previous["image"]
            deduped = True
    if not deduped:
        (session.dir / rel_image).write_bytes(image_bytes)

    passed = all(check["passed"] for check in assertions) if assertions else None
    frame = FrameRecord(
        index=index,
        at=now_iso(now),
        tool=tool,
        label=label,
        image=rel_image,
        target=target,
        redacted=redacted,
        kind=kind,
        assertion_passed=passed,
        phase=phase,
        pair_id=pair_id,
    )
    # A traceable call id without the OTel SDK: a frame is uniquely the Nth tool
    # call of this conversation.
    entry = frame.as_manifest_entry(tool_call_id=f"{session.id}/frame/{index}")
    # Content digest of the stored image: lets the next frame dedup against this
    # one, and doubles as an integrity check on the archived pixels.
    entry["image_sha256"] = digest
    if deduped:
        entry["deduped"] = True
    if assertions:
        entry["assertions"] = [dict(check) for check in assertions]
    if pii:
        entry["pii"] = [dict(finding) for finding in pii]
    manifest["frames"].append(entry)
    _write_manifest(session.manifest_path, manifest)
    return frame


def end_session(session: Session, *, now: dt.datetime | None = None) -> Path:
    """Close the session: mark the manifest complete and write its projections.

    Produces two views of the same trace next to the session: the static HTML
    filmstrip (for a human) and ``trace.otlp.json`` (OTLP/JSON, for an OTel
    backend — written to disk, never sent anywhere). Returns the filmstrip path.
    Idempotent enough to re-run: closing an already-closed session just refreshes
    ``ended_at`` and both projections.
    """
    manifest = _read_manifest(session.manifest_path)
    manifest["status"] = STATUS_COMPLETE
    manifest["ended_at"] = now_iso(now)
    _write_manifest(session.manifest_path, manifest)
    session.filmstrip_path.write_text(render_filmstrip(manifest), encoding="utf-8")
    _write_otlp(session, manifest)
    return session.filmstrip_path


# --- export -----------------------------------------------------------------

EXPORT_FORMATS = ("tar.gz", "zip")


def aggregate_pii(manifest: dict) -> dict[str, int]:
    """Total best-effort PII flags across a session's frames, ``{kind: count}``.

    Sums the per-frame ``pii`` lists (kind + count only — the values were never
    stored) so a caller can gate on residual risk before the trace leaves the
    machine. Empty when no frame was scanned or nothing was flagged. Pure.
    """
    totals: dict[str, int] = {}
    for frame in manifest.get("frames", []):
        for finding in frame.get("pii") or []:
            kind = finding.get("kind")
            if kind:
                totals[kind] = totals.get(kind, 0) + int(finding.get("count", 0))
    return totals


def export_session(session: Session, out_path: Path | None = None, *, fmt: str = "tar.gz") -> Path:
    """Bundle a session directory into one shareable archive; return its path.

    Packs the manifest, every frame, and (once the session is closed) the HTML
    filmstrip and OTLP/JSON — all under a single ``<session-id>/`` top-level
    folder so it extracts cleanly. ``fmt`` is ``"tar.gz"`` (default) or ``"zip"``.
    Without ``out_path`` the archive lands next to the session as
    ``<session-id>.<ext>``. Pure I/O — no Qt, no network.
    """
    if fmt not in EXPORT_FORMATS:
        raise RecordError(f"export format must be one of {EXPORT_FORMATS}, got {fmt!r}")
    if not session.dir.is_dir():
        raise SessionNotFound(f"session directory not found: {session.dir}")
    # Defence in depth against Zip/Tar Slip: ``start_session`` already validates
    # caller ids, but ``session.id`` can also come from a (possibly hand-edited)
    # manifest via ``resolve_session``. Refuse any id that is a path rather than a
    # name, so the archive's top-level folder can never traverse out on extract.
    top = session.id
    if os.sep in top or (os.altsep and os.altsep in top) or ".." in Path(top).parts:
        raise RecordError(f"unsafe session id for export: {session.id!r}")
    ext = "zip" if fmt == "zip" else "tar.gz"
    out_path = (
        Path(out_path) if out_path is not None else session.dir.parent / f"{session.id}.{ext}"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Explicit, sorted file list (no directories, no symlink surprises): every
    # entry is rooted under "<id>/" so the archive expands into its own folder.
    files = sorted(p for p in session.dir.rglob("*") if p.is_file())
    if fmt == "zip":
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                archive.write(path, arcname=f"{session.id}/{path.relative_to(session.dir)}")
    else:
        with tarfile.open(out_path, "w:gz") as archive:
            for path in files:
                archive.add(
                    path, arcname=f"{session.id}/{path.relative_to(session.dir)}", recursive=False
                )
    return out_path


# --- retention --------------------------------------------------------------


@dataclass(frozen=True)
class SessionSummary:
    """A read-only view of one session on disk, for listing and pruning."""

    id: str
    dir: Path
    started_at: str | None
    status: str | None
    frame_count: int
    size_bytes: int


def _dir_size(path: Path) -> int:
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file():
                total += child.stat().st_size
        except OSError:
            continue  # a file vanished mid-walk — ignore, this is a size estimate
    return total


def _parse_started(started_at: str | None) -> dt.datetime | None:
    if not started_at:
        return None
    try:
        moment = dt.datetime.fromisoformat(started_at)
    except ValueError:
        return None
    # Make naive timestamps comparable to the aware cutoff below.
    return moment.astimezone() if moment.tzinfo is None else moment


def list_sessions(records_root: Path | None = None) -> list[SessionSummary]:
    """Summarize every session under the records root, newest first.

    Skips directories without a readable manifest (a foreign or half-written
    dir is not a session), so this never raises on a messy records folder.
    """
    root = records_root if records_root is not None else _default_records_root()
    if not root.is_dir():
        return []
    summaries: list[SessionSummary] = []
    for child in sorted(root.iterdir()):
        manifest_path = child / MANIFEST_NAME
        if not manifest_path.is_file():
            continue
        try:
            manifest = _read_manifest(manifest_path)
        except RecordError:
            continue  # corrupt or future-version manifest: leave it alone
        summaries.append(
            SessionSummary(
                id=manifest.get("conversation_id", child.name),
                dir=child,
                started_at=manifest.get("started_at"),
                status=manifest.get("status"),
                frame_count=len(manifest.get("frames", [])),
                size_bytes=_dir_size(child),
            )
        )
    # Newest first; the time-prefixed id is a stable fallback when started_at
    # is missing, so the order is deterministic either way.
    summaries.sort(key=lambda s: (s.started_at or "", s.id), reverse=True)
    return summaries


def prune_sessions(
    records_root: Path | None = None,
    *,
    max_age_days: float | None = None,
    max_sessions: int | None = None,
    now: dt.datetime | None = None,
    dry_run: bool = False,
) -> list[SessionSummary]:
    """Delete archived sessions to cap the records folder's cost.

    Two independent limits, applied together (a session hit by either goes):
    ``max_age_days`` removes sessions started longer ago than that; ``max_sessions``
    keeps only the newest N. An in-progress (``recording``) session is never a
    candidate — only ``complete`` ones are reaped, so a live trace can't be pulled
    out from under the agent writing it. Returns the summaries that were (or, with
    ``dry_run``, would be) removed.
    """
    summaries = list_sessions(records_root)
    # Only finished sessions are eligible; recordings in flight are off-limits.
    complete = [s for s in summaries if s.status == STATUS_COMPLETE]

    doomed: dict[str, SessionSummary] = {}
    if max_sessions is not None and max_sessions >= 0:
        # complete is already newest-first (inherited from list_sessions order).
        for summary in complete[max_sessions:]:
            doomed[summary.id] = summary
    if max_age_days is not None:
        cutoff = (now or dt.datetime.now().astimezone()) - dt.timedelta(days=max_age_days)
        for summary in complete:
            started = _parse_started(summary.started_at)
            if started is not None and started < cutoff:
                doomed[summary.id] = summary

    removed = [s for s in summaries if s.id in doomed]
    if not dry_run:
        for summary in removed:
            shutil.rmtree(summary.dir, ignore_errors=True)
    return removed


def _write_otlp(session: Session, manifest: dict) -> None:
    """Write the OTLP/JSON projection of the trace next to the session."""
    from shotquill import __version__, otlp

    document = otlp.manifest_to_otlp(manifest, service_version=__version__)
    session.otlp_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# --- manifest I/O -----------------------------------------------------------


def _default_records_root() -> Path:
    from shotquill import paths

    return paths.records_dir()


def _load_open_manifest(session: Session) -> dict:
    manifest = _read_manifest(session.manifest_path)
    if manifest.get("status") == STATUS_COMPLETE:
        raise RecordError(
            f"session {session.id} is already closed; start a new one to record more frames"
        )
    return manifest


def _read_manifest(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SessionNotFound(f"cannot read session manifest {path}: {exc}") from exc
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RecordError(f"session manifest {path} is corrupt: {exc}") from exc
    version = manifest.get("shotquill_manifest_version")
    if version != MANIFEST_VERSION:
        raise RecordError(
            f"session manifest {path} is version {version!r}, "
            f"but this build understands version {MANIFEST_VERSION}"
        )
    manifest.setdefault("frames", [])
    return manifest


def _write_manifest(path: Path, manifest: dict) -> None:
    """Write the manifest atomically so a crash mid-frame can't truncate it."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# --- filmstrip --------------------------------------------------------------

_FILMSTRIP_CSS = """\
body { font: 14px system-ui, sans-serif; margin: 2rem; background: #1a1a1a; color: #eee; }
h1 { font-size: 1.2rem; } .meta { color: #999; margin-bottom: 1.5rem; }
.strip { display: flex; flex-wrap: wrap; gap: 1rem; align-items: flex-start; }
/* A before/after pair sits as one unit: its two frames side by side, set off by
   a subtle frame so the diff reads as a single step. */
.pair { display: flex; gap: .5rem; background: #202020; border: 1px solid #383838;
        border-radius: 10px; padding: .5rem; }
.pair .frame { width: 220px; }
.frame { background: #262626; border-radius: 8px; padding: .75rem; width: 280px; }
.frame .shot { position: relative; line-height: 0; }
.frame img { width: 100%; border-radius: 4px; display: block; background: #000; }
/* before/after change box: a percent-positioned outline over the after frame. */
.diffbox { position: absolute; border: 2px solid #f5a623;
           box-shadow: 0 0 0 1px rgba(0,0,0,.55); border-radius: 3px; pointer-events: none; }
.frame .tool { font-weight: 600; margin: .5rem 0 .25rem; }
.frame .label { color: #ccc; } .frame .at { color: #888; font-size: .8rem; }
.frame.failed { outline: 2px solid #c55; }
.frame.observation { opacity: .72; }
.frame.observation .tool { font-weight: 400; font-style: italic; color: #aaa; }
.badge { font-size: .7rem; padding: .1rem .4rem; border-radius: 4px; }
.badge.redacted { background: #2d4a2d; color: #9d9; }
.badge.pass { background: #234a2f; color: #9e9; }
.badge.fail { background: #5a2533; color: #f9a; }
.badge.obs { background: #33384a; color: #abd; }
.badge.phase { background: #4a3f23; color: #ed9; }
.empty { color: #888; }
"""


def _pct(value: object) -> str:
    """A frame-fraction in ``[0, 1]`` as a clamped CSS percentage (defensive)."""
    try:
        fraction = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        fraction = 0.0
    return f"{max(0.0, min(1.0, fraction)) * 100:.2f}%"


def render_filmstrip(manifest: dict) -> str:
    """Render the manifest as a self-contained static HTML filmstrip.

    Every app-supplied string (labels, tool names, capture targets, window
    titles inside ``target``) is HTML-escaped: an agent or the captured app
    could otherwise smuggle markup into a page the user opens in a browser.
    """

    def esc(value: object) -> str:
        return html.escape("" if value is None else str(value))

    sid = manifest.get("conversation_id", "")
    agent = manifest.get("agent") or {}
    agent_name = agent.get("name")
    status = manifest.get("status", "")
    started = manifest.get("started_at", "")
    ended = manifest.get("ended_at")
    frames = manifest.get("frames", [])

    def card(entry: dict) -> str:
        """Render one frame as a ``<figure>`` card."""
        span = entry.get("span") or {}
        tool = span.get("tool_name", "")
        is_observation = entry.get("kind") == "observation"
        badges = []
        if is_observation:
            badges.append('<span class="badge obs">observation</span>')
        phase = entry.get("phase")
        if phase in (PHASE_BEFORE, PHASE_AFTER):
            badges.append(f'<span class="badge phase">{esc(phase)}</span>')
        if entry.get("redacted"):
            badges.append('<span class="badge redacted">redacted</span>')
        passed = entry.get("assertion_passed")
        if passed is True:
            badges.append('<span class="badge pass">assert ok</span>')
        elif passed is False:
            badges.append('<span class="badge fail">assert FAIL</span>')
        # A failed assertion outlines the frame so it pops in the strip — the
        # failing step of a recorded test is the one a reviewer wants to find.
        # Observation frames dim back, so deliberate actions stay foreground.
        figure_class = "frame"
        if passed is False:
            figure_class += " failed"
        if is_observation:
            figure_class += " observation"
        label = entry.get("label")
        label_html = f'<div class="label">{esc(label)}</div>' if label else ""
        img = f'<img src="{esc(entry.get("image", ""))}" alt="{esc(label or tool)}" loading="lazy">'
        # A before/after change box (fractions of the frame) overlays the image as
        # a percent-positioned outline, so it tracks the image at any display size.
        diff = entry.get("diff")
        if isinstance(diff, dict):
            style = (
                f"left:{_pct(diff.get('x'))};top:{_pct(diff.get('y'))};"
                f"width:{_pct(diff.get('width'))};height:{_pct(diff.get('height'))}"
            )
            shot = f'<div class="shot">{img}<div class="diffbox" style="{style}"></div></div>'
        else:
            shot = f'<div class="shot">{img}</div>'
        return (
            f'<figure class="{figure_class}">'
            f"{shot}"
            f'<div class="tool">{esc(tool)} {" ".join(badges)}</div>'
            f"{label_html}"
            f'<div class="at">{esc(entry.get("at", ""))} · {esc(entry.get("target", ""))}</div>'
            "</figure>"
        )

    # Lay out cards in timeline order, but group each before/after pair into one
    # ``.pair`` block so the two halves sit side by side for a visual diff. A
    # ``before`` reserves the block at its own position; its matching ``after`` is
    # pulled up into that block (any frames captured between them keep their own
    # slots). A frame with no pair renders standalone.
    units: list[str | list[str]] = []  # str = standalone card; list = a pair's cards
    pair_blocks: dict[str, list[str]] = {}
    for entry in frames:
        pair_id = entry.get("pair_id")
        phase = entry.get("phase")
        if pair_id and phase == PHASE_BEFORE:
            block = [card(entry)]
            pair_blocks[pair_id] = block
            units.append(block)
        elif pair_id and phase == PHASE_AFTER and pair_id in pair_blocks:
            pair_blocks[pair_id].append(card(entry))
        else:
            units.append(card(entry))

    parts = []
    for unit in units:
        if isinstance(unit, list):
            parts.append('<div class="pair">\n' + "\n".join(unit) + "\n</div>")
        else:
            parts.append(unit)
    strip = "\n".join(parts) if parts else '<p class="empty">No frames recorded.</p>'

    meta_bits = [f"{len(frames)} frame(s)", f"status: {esc(status)}", f"started {esc(started)}"]
    if ended:
        meta_bits.append(f"ended {esc(ended)}")
    if agent_name:
        meta_bits.insert(0, f"agent: {esc(agent_name)}")

    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">\n'
        f"<title>ShotQuill recording {esc(sid)}</title>\n"
        f"<style>{_FILMSTRIP_CSS}</style></head><body>\n"
        f"<h1>ShotQuill recording <code>{esc(sid)}</code></h1>\n"
        f'<div class="meta">{" · ".join(meta_bits)}</div>\n'
        f'<div class="strip">\n{strip}\n</div>\n'
        "</body></html>\n"
    )
