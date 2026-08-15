# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Single source of truth for the command surface (CLI + MCP).

Every command and every input parameter is declared once, here, as data. The
CLI argparse tree (:mod:`shotquill.cli`) and the MCP tool descriptors
(:mod:`shotquill.mcp`) are both *generated* from this registry, so the two
front-ends can never drift apart in name, parameter set, or type — the bug class
that let ``windows`` / ``list_windows`` and ``record start`` / ``record_start``
diverge by hand. ``build_argparse`` / ``build_mcp_tools`` also assert their
handler maps cover the registry exactly, so a missing or orphaned handler key
fails fast at startup rather than silently.

Boundary (deliberate): this module owns the *shared, cross-surface* contract —
command names, input parameters, types, descriptions, and MCP annotations.
Surface-exclusive concerns stay with their surface: the CLI keeps its exit-code
handling and stdout/`-` streaming in :mod:`shotquill.cli`; the MCP
``outputSchema`` fragments (which have no CLI counterpart and so cannot drift
against it) stay in :mod:`shotquill.mcp` as ``OUTPUT_SCHEMAS``.

Naming model (see the CLI/MCP naming review): the CLI uses ``noun verb``
subcommands (``window list``, ``session start``); each MCP tool name is those
same tokens joined by ``_`` (``window_list``, ``session_start``) — the
resource-prefix style used by the git / playwright / newer GitHub MCP servers,
and SEP-986-conforming (snake_case, no spaces). One definition, both names.
"""

from __future__ import annotations

from dataclasses import dataclass

from shotquill.headless import (
    SCROLL_CLICKS_DEFAULT,
    SCROLL_INTERVAL_DEFAULT,
    SCROLL_MAX_HEIGHT_DEFAULT,
)

# Shown in every CLI ``--help``: agents discover the exit-code contract the same
# way they discover the flags. Kept here so cli.py and the docs generator share
# one copy.
EXIT_CODE_EPILOG = (
    "exit codes: 0 ok; errors 1-19 (1 error, 2 usage, 3 permission denied, "
    "4 capability unavailable on this platform/session, 5 no window or display "
    "matched, 6 blocked by the blocklist or not on the allowlist, 7 invalid input); "
    "assertion results 20+ (20 OCR assertion failed)"
)


def _rect_schema() -> dict:
    """A *fresh* logical-rectangle object schema (region / mask / reveal in MCP).

    Built anew per call rather than shared by reference, so a future per-tool
    tweak to one rect schema can't alias-corrupt every other tool's rect."""
    return {
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "width": {"type": "integer"},
            "height": {"type": "integer"},
        },
        "required": ["x", "y", "width", "height"],
    }


@dataclass(frozen=True)
class Param:
    """One input parameter, carrying both its CLI and MCP facets.

    ``kind`` drives both generators:
      str | int | float  scalar; CLI ``type=``, MCP integer/number/string
      flag               CLI ``store_true``; MCP boolean (default false)
      str_list           CLI repeatable ``append``; MCP array of strings
      rect               ``x,y,w,h`` string on the CLI; ``{x,y,w,h}`` object in MCP
      rect_list          repeatable rect; MCP array of rect objects
      phase              CLI ``--before``/``--after`` mutex pair into ``dest``;
                         MCP single ``enum`` of ["before","after"]
    """

    name: str  # snake_case canonical key: argparse dest and MCP property name
    help: str  # shared one-line description
    kind: str = "str"
    cli_flags: tuple[str, ...] = ()  # explicit CLI flags; default: --<name-with-dashes>
    positional: bool = False
    nargs: str | None = None
    metavar: str | None = None
    choices: tuple[str, ...] = ()
    default: object = None
    required: bool = False
    group: str | None = None  # CLI mutually-exclusive group id (handler enforces it for MCP)
    group_required: bool = False  # the CLI mutex group must have exactly one member supplied
    cli_only: bool = False
    mcp_only: bool = False
    mcp_name: str | None = None  # per-surface key override (e.g. CLI --output -> MCP save_path)
    mcp_help: str | None = None  # richer agent-facing description when it should differ
    # For kind="phase": per-choice CLI flag help, e.g. {"before": "...", "after": "..."}. Kept on
    # the Param so the CLI flag help and the MCP enum description have one source, not two.
    subhelp: dict | None = None

    @property
    def dashed(self) -> str:
        return "--" + self.name.replace("_", "-")

    @property
    def flags(self) -> tuple[str, ...]:
        return self.cli_flags or (self.dashed,)

    @property
    def schema_name(self) -> str:
        return self.mcp_name or self.name


@dataclass(frozen=True)
class Command:
    """One leaf command, addressed by its CLI path (1 or 2 tokens)."""

    cli_path: tuple[str, ...]  # ("capture",) or ("window","list") or ("session","start")
    summary: str  # CLI help (the one-line subcommand listing)
    params: tuple[Param, ...] = ()
    handler: str = ""  # key the front-ends look up to attach their own callable
    exit_epilog: bool = True  # include EXIT_CODE_EPILOG in CLI help
    description: str | None = None  # fuller CLI --help body; falls back to summary
    # MCP facet — a command is exposed over MCP iff mcp_name is set.
    mcp_name: str | None = None
    mcp_description: str | None = None  # agent-facing; falls back to summary
    mcp_annotations: dict | None = None

    @property
    def mcp_params(self) -> tuple[Param, ...]:
        return tuple(p for p in self.params if not p.cli_only)


# --------------------------------------------------------------------------- #
# Shared parameter groups
# --------------------------------------------------------------------------- #


def _target_params() -> tuple[Param, ...]:
    """What-to-capture options, shared by capture / ocr / session frame.

    window_id/app/region/display are mutually exclusive (CLI group "target",
    handler-enforced for MCP); title narrows app and is separate.
    """
    return (
        Param(
            "window_id",
            "exact window id (see `squill window list`)",
            kind="int",
            group="target",
            mcp_help="Exact window id from window_list. Mutually exclusive with app/region.",
        ),
        Param(
            "app",
            "pick the front-most window of a matching app (substring)",
            group="target",
            mcp_help="Case-insensitive substring of the owning app's name; the front-most "
            "matching window is captured (ambiguity is reported in the result).",
        ),
        Param(
            "title",
            "narrow --app matches by title substring",
            mcp_help="Narrow app matches by window-title substring (requires app).",
        ),
        Param(
            "region",
            "logical-coordinate rectangle as x,y,w,h",
            kind="rect",
            group="target",
            mcp_help="Rectangle in logical screen coordinates. "
            "Mutually exclusive with window_id/app/display.",
        ),
        Param(
            "display",
            "capture one monitor by index (see `squill display list`; 0 = primary)",
            kind="int",
            metavar="N",
            group="target",
            mcp_help="Capture one monitor by index from display_list (0 = primary). "
            "Mutually exclusive with window_id/app/region.",
        ),
    )


_MASK = Param(
    "mask",
    "black out a rectangle (image-relative logical coords) before output; repeatable. "
    "A caller-controlled redaction layered on the blocklist.",
    kind="rect_list",
    metavar="X,Y,W,H",
    mcp_help="Rectangles to black out before the frame is used anywhere, in the captured "
    "frame's own logical coordinates (0,0 = its top-left). A caller-controlled redaction "
    "layered on the blocklist.",
)

_REVEAL = Param(
    "reveal",
    "mosaic the whole frame, keeping only these rectangle(s) sharp; repeatable. "
    "Minimizes exposure to just the action (image-relative coords).",
    kind="rect_list",
    metavar="X,Y,W,H",
    mcp_help="Mosaic the whole frame, keeping only these rectangle(s) sharp — minimize "
    "exposure to just the action. Same coordinate space as mask.",
)

_REDACT_PII = Param(
    "redact_pii",
    "OCR the frame and mask the pixels of any likely PII (email, card, SSN, …) before "
    "output; best-effort, not a guarantee",
    kind="flag",
    mcp_help="OCR the frame and mask the pixels of any likely PII (email, credit card, SSN, "
    "IBAN, IPv4, phone) before the frame is used anywhere. Best-effort, not a guarantee.",
)

_CONTAINS = Param(
    "contains",
    "assert the recognized text contains TEXT (repeatable; all must hold)",
    kind="str_list",
    metavar="TEXT",
)
_MATCHES = Param(
    "matches",
    "assert the recognized text matches REGEX (repeatable; all must hold)",
    kind="str_list",
    metavar="REGEX",
)
_IGNORE_CASE = Param(
    "ignore_case",
    "make --contains / --matches case-insensitive (OCR case is noisy)",
    kind="flag",
    cli_flags=("-i", "--ignore-case"),
)


def _json() -> Param:
    """The CLI-only ``--json`` flag (MCP responses are always structured)."""
    return Param("json", "machine-readable output", kind="flag", cli_only=True)


def _app_rule_selector() -> tuple[Param, ...]:
    """The bundle-id / name choice shared by blocklist & allowlist add/remove."""
    return (
        Param(
            "bundle_id",
            "match the owning app's bundle id exactly",
            group="rule",
            group_required=True,
            cli_only=True,
        ),
        Param(
            "name",
            "match the app name as a case-insensitive substring",
            group="rule",
            group_required=True,
            cli_only=True,
        ),
    )


def _session_arg(summary: str) -> Param:
    """The required positional ``session`` handle, shared by frame / end / export.

    One definition so the three subcommands can't drift in help wording the way
    they had started to (``export`` once read differently from ``frame``/``end``).
    """
    return Param(
        "session",
        summary,
        positional=True,
        required=True,
        mcp_help="conversation_id (or directory) from session_start.",
    )


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #

REGISTRY: tuple[Command, ...] = (
    Command(
        cli_path=("capture",),
        summary="capture the screen, a window, or a region",
        mcp_name="capture",
        mcp_description=(
            "Take a screenshot of the full screen (default), one window (by window_id, or "
            "by app/title match), one monitor (by display index), or a region. Set scrolling "
            "with a region to drive the wheel and stitch a long screenshot. Returns the image "
            "plus a JSON metadata text block. Use max_width (e.g. 1024) to downscale large "
            "screens and save context. Pass session (a handle from session_start) to also file "
            "this capture as an observation frame in that recording."
        ),
        mcp_annotations={"title": "Take a screenshot", "openWorldHint": False},
        params=(
            *_target_params(),
            Param(
                "interactive",
                "frame the shot interactively — the compositor's own picker chooses a window, "
                "region, or screen (Wayland only for now; meant for a compositor-bound hotkey "
                "where global key grabs are blocked)",
                kind="flag",
                cli_only=True,
            ),
            Param(
                "scrolling",
                "long screenshot: sample --region while you scroll within it and stitch the "
                "frames into one tall image (manual scroll, or --auto to drive the wheel; stops "
                "when the view settles or --max-height is reached)",
                kind="flag",
                mcp_help=("With region, drive the wheel and stitch a long screenshot."),
            ),
            Param(
                "auto",
                "with --scrolling, drive the scroll automatically by synthesizing the mouse "
                "wheel (unavailable on Wayland until a ScreenCast/PipeWire capture path exists). "
                "Point at the area to scroll before it starts.",
                kind="flag",
                cli_only=True,
            ),
            Param(
                "max_height",
                f"cap the stitched long screenshot's height (--scrolling only; "
                f"default {SCROLL_MAX_HEIGHT_DEFAULT})",
                kind="int",
                metavar="PX",
                default=SCROLL_MAX_HEIGHT_DEFAULT,
                mcp_help="Maximum stitched image height in pixels.",
            ),
            Param(
                "scroll_interval",
                f"seconds between samples while scrolling (--scrolling only; "
                f"default {SCROLL_INTERVAL_DEFAULT})",
                kind="float",
                metavar="SEC",
                default=SCROLL_INTERVAL_DEFAULT,
                mcp_help="Seconds to wait between automatic scroll samples.",
            ),
            Param(
                "scroll_clicks",
                f"wheel notches to turn per step in --auto mode (default {SCROLL_CLICKS_DEFAULT})",
                kind="int",
                metavar="N",
                default=SCROLL_CLICKS_DEFAULT,
                mcp_help="Mouse-wheel notches to turn between automatic samples.",
            ),
            Param(
                "output",
                "output file path, or '-' for image bytes on stdout (default: temp dir)",
                cli_flags=("-o", "--output"),
                mcp_name="save_path",
                mcp_help="Also write the image to this file path.",
            ),
            Param(
                "format",
                "output image format (default: png)",
                kind="str",
                choices=("png", "jpg"),
                default="png",
            ),
            Param(
                "max_width",
                "downscale to at most this many pixels wide (keeps aspect ratio)",
                kind="int",
                metavar="PX",
                mcp_help="Downscale to at most this many pixels wide.",
            ),
            _json(),
            Param(
                "include_cursor",
                "composite the pointer (best effort)",
                kind="flag",
                cli_only=True,
            ),
            Param(
                "deterministic",
                "byte-stable output for golden-image/diff tests: pin the embedded DPI and strip "
                "PNG timestamp/text chunks (forces the cursor off)",
                kind="flag",
                mcp_help="Byte-stable output for golden-image/diff tests: pin the embedded DPI "
                "and strip PNG timestamp/text chunks so identical pixels always encode "
                "to identical bytes.",
            ),
            Param(
                "session",
                "also file this capture as an observation frame in a recording session "
                "(handle from `session start`)",
                mcp_help="When given (a handle from session_start), also file this capture as an "
                "observation frame in that recording. Frames go to disk, not into your context.",
            ),
            Param(
                "dedup",
                "when filing the observation frame (with --session), reference the previous frame "
                "instead of writing a duplicate if the screen is unchanged",
                kind="flag",
                mcp_help="When filing into a session, reference the previous frame instead of "
                "writing a duplicate if the screen is unchanged (cost control).",
            ),
            _MASK,
            _REVEAL,
            _REDACT_PII,
        ),
        handler="capture",
    ),
    Command(
        cli_path=("window", "list"),
        summary="list on-screen windows, front-most first",
        mcp_name="window_list",
        mcp_description=(
            "List on-screen windows, front-most first: id, owning app, title, and bounds. "
            "Ids feed capture/ocr window_id. May be unavailable on some platforms (e.g. "
            "Wayland) — see doctor."
        ),
        mcp_annotations={
            "title": "List on-screen windows",
            "readOnlyHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        params=(_json(),),
        handler="window_list",
    ),
    Command(
        cli_path=("display", "list"),
        summary="list monitors and their indexes (for `capture --display N`)",
        mcp_name="display_list",
        mcp_description=(
            "List the monitors of this machine: index (primary first), name, logical bounds "
            "on the virtual desktop, pixel scale. Indexes feed capture/ocr display for a "
            "one-monitor shot."
        ),
        mcp_annotations={
            "title": "List monitors",
            "readOnlyHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        params=(_json(),),
        handler="display_list",
    ),
    Command(
        cli_path=("ocr",),
        summary="extract text from an image file, stdin, or straight off the screen (on-device)",
        mcp_name="ocr",
        mcp_description=(
            "Extract text with on-device OCR, and optionally assert on it. Pass path for an "
            "existing image file, or the capture target arguments (none = full screen) to "
            "capture-and-recognize in memory — only text is returned, costing no image tokens. "
            "Add contains/matches to check the screen and read `passed` in the result."
        ),
        mcp_annotations={
            "title": "Read or assert on text on the screen",
            "readOnlyHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        params=(
            Param(
                "path",
                "image file, or '-' for image bytes on stdin; omit to capture-and-recognize in "
                "one step (target options below pick what, like `capture`)",
                positional=True,
                nargs="?",
                mcp_help="Image file to recognize. Exclusive with the capture targets.",
            ),
            *_target_params(),
            _CONTAINS,
            _MATCHES,
            _IGNORE_CASE,
            Param(
                "boxes",
                "print each line as 'x,y,w,h<TAB>text' (pixel box in the image) and report where "
                "any --contains / --matches landed",
                kind="flag",
                mcp_help="Also return each line's pixel bounding box in the image, and locate "
                "where any contains/matches landed. Coordinates are image pixels, top-left origin.",
            ),
        ),
        handler="ocr",
    ),
    Command(
        cli_path=("diff",),
        summary="compare two images and report where they differ (for golden-image checks)",
        mcp_name="diff",
        mcp_description=(
            "Compare two images and report whether (and where) they differ — for golden-image "
            "checks. Returns changed plus the bounding box of the change and both sizes. Raise "
            "threshold to absorb anti-aliasing/compression noise."
        ),
        mcp_annotations={
            "title": "Diff two images",
            "readOnlyHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        params=(
            Param(
                "a",
                "first image file (or '-' for image bytes on stdin)",
                positional=True,
                required=True,
            ),
            Param(
                "b",
                "second image file (or '-' for image bytes on stdin)",
                positional=True,
                required=True,
            ),
            Param(
                "threshold",
                "per-channel delta that counts as a change (0 = exact; raise to absorb "
                "anti-aliasing/compression noise)",
                kind="int",
                default=0,
                metavar="N",
            ),
            _json(),
        ),
        handler="diff",
    ),
    Command(
        cli_path=("doctor",),
        summary="report platform capabilities and permissions",
        mcp_name="doctor",
        mcp_description=(
            "Report this host's capability/permission matrix (capture, window_list, ocr, "
            "screen-recording permission) with reasons for anything unavailable."
        ),
        mcp_annotations={
            "title": "Capability & permission report",
            "readOnlyHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        params=(_json(),),
        handler="doctor",
    ),
    # --- session (the flight recorder) -------------------------------------- #
    Command(
        cli_path=("session", "start"),
        summary="open a session; prints its directory (pass it back as the session handle)",
        mcp_name="session_start",
        mcp_description=(
            "Open a flight-recorder session: a trace of frames you leave behind as you operate "
            "the screen, for a human or a reviewing AI to replay later. Returns conversation_id "
            "— pass it as session to session_frame / session_end. Frames go to disk, not into "
            "your context."
        ),
        mcp_annotations={"title": "Start a recording session", "openWorldHint": False},
        params=(
            Param("label", "human-readable note for the whole session"),
            Param("agent", "name of the agent being recorded (gen_ai.agent.name)"),
            Param("agent_id", "stable id of the agent (gen_ai.agent.id)"),
            Param(
                "session_id",
                "set the conversation id (default: generated)",
                cli_flags=("--id",),
                mcp_name="id",
            ),
            Param(
                "dir", "pin the session directory (default: a generated dir under the data folder)"
            ),
            _json(),
        ),
        handler="session_start",
    ),
    Command(
        cli_path=("session", "frame"),
        summary="capture one frame into a session (redaction stays on)",
        mcp_name="session_frame",
        mcp_description=(
            "Capture one frame into a session (full screen by default, or a window/region/monitor "
            "via the target args). Blocklist redaction stays on. The image is written to the "
            "session on disk and is NOT returned to you — use capture when you want to see the "
            "pixels. Add contains/matches to OCR the frame and record the assertion in the trace; "
            "read assertion_passed to branch."
        ),
        mcp_annotations={"title": "Record one frame", "openWorldHint": False},
        params=(
            _session_arg("session handle from `session start`"),
            Param("tool", "the action this frame documents (gen_ai.tool.name)", required=True),
            Param("label", "human-readable note for this frame"),
            *_target_params(),
            _CONTAINS,
            _MATCHES,
            _IGNORE_CASE,
            _MASK,
            _REVEAL,
            Param(
                "scan_pii",
                "OCR the frame and flag likely PII kinds + counts on it (best-effort, not a "
                "guarantee; records the kind/count only, never the value)",
                kind="flag",
            ),
            _REDACT_PII,
            Param(
                "phase",
                "file this frame as one half of a before/after pair around an action",
                kind="phase",
                mcp_help="File this frame as one half of a before/after pair around an action: "
                "'before' opens a pair, 'after' joins the most recent open one (a lone 'after' is "
                "an error). Lets a reviewer diff what changed when the agent acted.",
                subhelp={
                    "before": "file this frame as the 'before' half of a before/after pair "
                    "around an action",
                    "after": "file this frame as the 'after' half, paired with the most recent "
                    "--before",
                },
            ),
            Param(
                "dedup",
                "if this frame is identical to the previous one, reference it instead of writing "
                "a duplicate image (cost control)",
                kind="flag",
            ),
            Param(
                "max_dimension",
                "cap the frame's longer edge to PX pixels before filing (0 = keep native size)",
                kind="int",
                default=0,
                metavar="PX",
            ),
            _json(),
        ),
        handler="session_frame",
    ),
    Command(
        cli_path=("session", "end"),
        summary="close a session and render its HTML filmstrip",
        mcp_name="session_end",
        mcp_description=(
            "Close a session and render its static HTML filmstrip. Returns the manifest and "
            "filmstrip paths plus the frame count."
        ),
        mcp_annotations={"title": "End a recording session", "openWorldHint": False},
        params=(
            _session_arg("session handle from `session start`"),
            _json(),
        ),
        handler="session_end",
    ),
    Command(
        cli_path=("session", "list"),
        summary="list recorded sessions (newest first) with size and frame count",
        mcp_name="session_list",
        mcp_description=(
            "List recorded sessions, newest first: conversation_id, directory, start time, "
            "status, frame count, and on-disk size in bytes. Use it to find sessions to prune."
        ),
        mcp_annotations={
            "title": "List recorded sessions",
            "readOnlyHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        params=(_json(),),
        handler="session_list",
    ),
    Command(
        cli_path=("session", "prune"),
        summary="delete old recorded sessions to cap disk cost (complete sessions only)",
        mcp_name="session_prune",
        mcp_description=(
            "Delete old recorded sessions to cap disk cost. Give max_age_days and/or "
            "max_sessions (at least one); only completed sessions are eligible, so a live "
            "recording is never removed. Pass dry_run to see what would go without deleting."
        ),
        mcp_annotations={
            "title": "Prune old recordings",
            "destructiveHint": True,
            "openWorldHint": False,
        },
        params=(
            Param(
                "max_age_days",
                "remove sessions started more than DAYS ago",
                kind="float",
                metavar="DAYS",
            ),
            Param("max_sessions", "keep only the newest N sessions", kind="int", metavar="N"),
            Param("dry_run", "report what would be removed without deleting anything", kind="flag"),
            _json(),
        ),
        handler="session_prune",
    ),
    Command(
        cli_path=("session", "export"),
        summary="bundle a session into one shareable archive (manifest + frames + filmstrip)",
        mcp_name="session_export",
        mcp_description=(
            "Bundle a session into one shareable archive (manifest + frames + filmstrip + OTLP). "
            "Set fail_on_pii to refuse when any frame carries a best-effort PII flag. Returns the "
            "archive path; the result also reports any residual PII so you can decide before "
            "sharing."
        ),
        mcp_annotations={"title": "Export a recording session", "openWorldHint": False},
        params=(
            _session_arg("session id or directory (from `session start`)"),
            Param(
                "output",
                "archive path to write (default: <session-id>.<ext> next to the session)",
                cli_flags=("-o", "--output"),
                mcp_help="Archive path to write (default: beside the session).",
            ),
            Param(
                "format",
                "archive format (default: tar.gz)",
                choices=("tar.gz", "zip"),
                default="tar.gz",
            ),
            Param(
                "fail_on_pii",
                "refuse to export (exit 6) if any frame carries a best-effort "
                "PII flag (from `session frame --scan-pii`)",
                kind="flag",
                mcp_help="Refuse to export if any frame carries a PII flag.",
            ),
            _json(),
        ),
        handler="session_export",
    ),
    # --- CLI-only commands -------------------------------------------------- #
    Command(
        cli_path=("mcp",),
        summary="serve the MCP stdio protocol (for AI agent hosts)",
        exit_epilog=False,
        params=(
            Param(
                "timeout",
                "exit after this many seconds (bound the session; default: until EOF)",
                kind="int",
                metavar="SECONDS",
            ),
        ),
        handler="mcp",
    ),
    Command(
        cli_path=("uninstall",),
        summary="uninstall ShotQuill on macOS while preserving user data",
        description=(
            "Detect whether Homebrew or the direct macOS PKG owns ShotQuill, preview the exact "
            "removal plan, and delegate to that installer. Settings, recordings, logs, "
            "screenshots, and custom save folders are always preserved. Without --yes an "
            "interactive terminal asks for confirmation; execution always requires a terminal. "
            "This command is not exposed over MCP."
        ),
        params=(
            Param("dry_run", "show the removal plan without changing anything", kind="flag"),
            Param(
                "yes",
                "confirm the displayed plan without a prompt (still requires a terminal)",
                kind="flag",
            ),
        ),
        handler="uninstall",
    ),
    Command(
        cli_path=("desktop", "install"),
        summary="install the Linux .desktop entry and icon under ~/.local/share",
        description=(
            "Copy the bundled .desktop launcher and icon to ~/.local/share so ShotQuill shows "
            "up in the GNOME / KDE / XFCE application menu. Needed after `pipx install shotquill`, "
            "because pipx puts data-files inside its private venv where the desktop never looks. "
            "Idempotent."
        ),
        params=(
            Param(
                "print_paths",
                "show the resolved source and destination paths, then exit (no copy)",
                kind="flag",
            ),
        ),
        handler="desktop_install",
    ),
    Command(
        cli_path=("blocklist", "list"),
        summary="show the current rules",
        exit_epilog=False,
        params=(_json(),),
        handler="blocklist_list",
    ),
    Command(
        cli_path=("blocklist", "add"),
        summary="add a rule",
        exit_epilog=False,
        params=_app_rule_selector(),
        handler="blocklist_add",
    ),
    Command(
        cli_path=("blocklist", "remove"),
        summary="remove a matching rule",
        exit_epilog=False,
        params=_app_rule_selector(),
        handler="blocklist_remove",
    ),
    Command(
        cli_path=("allowlist", "list"),
        summary="show whether enabled and the current rules",
        exit_epilog=False,
        params=(_json(),),
        handler="allowlist_list",
    ),
    Command(
        cli_path=("allowlist", "add"),
        summary="add a rule",
        exit_epilog=False,
        params=_app_rule_selector(),
        handler="allowlist_add",
    ),
    Command(
        cli_path=("allowlist", "remove"),
        summary="remove a matching rule",
        exit_epilog=False,
        params=_app_rule_selector(),
        handler="allowlist_remove",
    ),
    Command(
        cli_path=("allowlist", "enable"),
        summary="turn the allowlist on (only listed apps can then be captured)",
        exit_epilog=False,
        params=(),
        handler="allowlist_enable",
    ),
    Command(
        cli_path=("allowlist", "disable"),
        summary="turn the allowlist off (capture normally)",
        exit_epilog=False,
        params=(),
        handler="allowlist_disable",
    ),
)


# Group-level help (the parent of a two-token command, e.g. `squill session …`).
GROUP_HELP: dict[str, str] = {
    "window": "inspect on-screen windows",
    "display": "inspect monitors",
    "session": "record a session of frames an agent leaves behind (a flight recorder)",
    "desktop": "desktop-integration helpers",
    "blocklist": "manage the app blocklist (apps that are never captured)",
    "allowlist": "manage the capture allowlist (when enabled, ONLY these apps are captured)",
}


def commands() -> tuple[Command, ...]:
    return REGISTRY


def mcp_commands() -> tuple[Command, ...]:
    return tuple(c for c in REGISTRY if c.mcp_name is not None)


# --------------------------------------------------------------------------- #
# Generators
# --------------------------------------------------------------------------- #

_ROOT_DESCRIPTION = "Screenshot & OCR for scripts and agents (run bare for the GUI)."


def build_argparse(version: str, handlers: dict):
    """Build the whole ``squill`` argparse tree from the registry.

    ``handlers`` maps each command's ``handler`` key to the callable that runs
    it; cli.py owns those (the registry stays pure data).
    """
    import argparse

    _check_handler_map(handlers, {c.handler for c in REGISTRY}, "CLI handler map")

    parser = argparse.ArgumentParser(
        prog="squill", description=_ROOT_DESCRIPTION, epilog=EXIT_CODE_EPILOG
    )
    parser.add_argument("--version", action="version", version=f"shotquill {version}")
    top = parser.add_subparsers(dest="command", required=True)

    group_subs: dict[str, object] = {}
    for cmd in REGISTRY:
        if len(cmd.cli_path) == 1:
            leaf_parent = top
            name = cmd.cli_path[0]
        else:
            grp, name = cmd.cli_path
            if grp not in group_subs:
                gp = top.add_parser(grp, help=GROUP_HELP.get(grp, ""))
                group_subs[grp] = gp.add_subparsers(dest=f"{grp}_command", required=True)
            leaf_parent = group_subs[grp]
        sub = leaf_parent.add_parser(
            name,
            help=cmd.summary,
            description=cmd.description or cmd.summary,
            epilog=EXIT_CODE_EPILOG if cmd.exit_epilog else None,
        )
        _add_cli_params(sub, cmd)
        sub.set_defaults(func=handlers[cmd.handler])
    return parser


def _check_handler_map(handlers: dict, expected: set, label: str) -> None:
    """Fail fast on registry/handler drift in either direction.

    The bare ``handlers[key]`` index already KeyErrors on a *missing* handler;
    this also catches the silent direction — an orphan/misspelled handler key
    that no registry command references (the kind of drift this module exists to
    prevent, but in the data the generators consume rather than produce)."""
    keys = set(handlers)
    if keys != expected:
        raise RuntimeError(
            f"{label} mismatch: missing {sorted(expected - keys)}, orphan {sorted(keys - expected)}"
        )


def _add_cli_params(parser, cmd: Command) -> None:
    # A mutex group is required iff any of its members declares group_required,
    # so the rule lives on the params, not in a separate parallel set.
    required_groups = {p.group for p in cmd.params if p.group and p.group_required}
    groups: dict[str, object] = {}
    for p in cmd.params:
        if p.mcp_only:
            continue  # surface-exclusive to MCP; never on the CLI tree
        if p.kind == "phase":
            _add_phase(parser, p)
            continue
        if p.group:
            if p.group not in groups:
                groups[p.group] = parser.add_mutually_exclusive_group(
                    required=p.group in required_groups
                )
            _add_cli_one(groups[p.group], p, in_group=True)
        else:
            _add_cli_one(parser, p, in_group=False)


def _add_phase(parser, p: Param) -> None:
    # The before/after flag help comes from the Param's subhelp, so the CLI flags
    # and the MCP enum description (p.mcp_help) have one source.
    sub = p.subhelp or {}
    grp = parser.add_mutually_exclusive_group()
    grp.add_argument(
        "--before", dest=p.name, action="store_const", const="before", help=sub.get("before")
    )
    grp.add_argument(
        "--after", dest=p.name, action="store_const", const="after", help=sub.get("after")
    )


def _add_cli_one(target, p: Param, *, in_group: bool) -> None:
    if p.positional:
        kwargs: dict = {"help": p.help}
        if p.nargs:
            kwargs["nargs"] = p.nargs
        if p.kind == "int":
            kwargs["type"] = int
        elif p.kind == "float":
            kwargs["type"] = float
        if p.metavar:
            kwargs["metavar"] = p.metavar
        target.add_argument(p.name, **kwargs)
        return

    kwargs = {"dest": p.name, "help": p.help}
    if p.kind == "flag":
        kwargs["action"] = "store_true"
    elif p.kind in ("str_list", "rect_list"):
        kwargs["action"] = "append"
        kwargs["metavar"] = p.metavar
    else:
        if p.kind == "int":
            kwargs["type"] = int
        elif p.kind == "float":
            kwargs["type"] = float
        if p.metavar:
            kwargs["metavar"] = p.metavar
        if p.choices:
            kwargs["choices"] = p.choices
        if p.default is not None:
            kwargs["default"] = p.default
    if p.required and not in_group:
        kwargs["required"] = True
    target.add_argument(*p.flags, **kwargs)


def _mcp_property(p: Param) -> dict:
    if p.kind in ("int",):
        prop: dict = {"type": "integer"}
    elif p.kind == "float":
        prop = {"type": "number"}
    elif p.kind == "flag":
        prop = {"type": "boolean", "default": False}
    elif p.kind == "str_list":
        prop = {"type": "array", "items": {"type": "string"}}
    elif p.kind == "rect":
        prop = _rect_schema()
    elif p.kind == "rect_list":
        prop = {"type": "array", "items": _rect_schema()}
    elif p.kind == "phase":
        prop = {"type": "string", "enum": ["before", "after"]}
    else:
        prop = {"type": "string"}
    if p.choices:
        prop["enum"] = list(p.choices)
    if p.default is not None and p.kind != "flag":
        prop["default"] = p.default
    desc = p.mcp_help or p.help
    if desc:
        prop["description"] = desc
    return prop


def build_mcp_tools(handlers: dict, output_schemas: dict) -> dict:
    """Build the MCP ``_TOOLS`` registry (name -> {handler, descriptor}).

    ``handlers`` maps each command's ``mcp_name`` to its callable; mcp.py owns
    those and supplies ``output_schemas`` (the MCP-only outputSchema fragments).
    """
    mcp_names = {c.mcp_name for c in mcp_commands()}
    _check_handler_map(handlers, mcp_names, "MCP handler map")
    # Every MCP tool must ship an outputSchema (the typed-structuredContent
    # contract the agent ergonomics rely on); an orphan/missing key would
    # otherwise drop a tool's outputSchema silently.
    _check_handler_map(output_schemas, mcp_names, "MCP OUTPUT_SCHEMAS")

    tools: dict[str, dict] = {}
    for cmd in mcp_commands():
        props: dict[str, dict] = {}
        required: list[str] = []
        for p in cmd.mcp_params:
            props[p.schema_name] = _mcp_property(p)
            if p.required:
                required.append(p.schema_name)
        input_schema: dict = {
            "type": "object",
            "properties": props,
            "additionalProperties": False,
        }
        if required:
            input_schema["required"] = required
        annotations = dict(cmd.mcp_annotations or {})
        descriptor: dict = {
            "name": cmd.mcp_name,
            "description": cmd.mcp_description or cmd.summary,
            "annotations": annotations,
            "inputSchema": input_schema,
        }
        # Top-level title (SEP/BaseMetadata) mirrors the annotation title so hosts
        # that read either one get a human label.
        if annotations.get("title"):
            descriptor["title"] = annotations["title"]
        out = output_schemas.get(cmd.mcp_name)
        if out is not None:
            descriptor["outputSchema"] = out
        tools[cmd.mcp_name] = {"handler": handlers[cmd.mcp_name], "descriptor": descriptor}
    return tools
