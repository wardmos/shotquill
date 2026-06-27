# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Headless capture operations shared by the CLI and the (future) MCP server.

This is the seam the two front-ends meet at: all real logic lives here as
plain functions over the ``ScreenCapturer`` / ``TextRecognizer`` abstractions,
so the CLI stays a thin argparse layer and MCP can later wrap the same calls
instead of shelling out.

Errors are typed and carry the documented CLI exit codes, because agents
branch on them: 3 permission, 4 capability unavailable on this platform or
session, 5 no window or display matched, 7 input the caller supplied is
invalid (e.g. an image past the size cap).
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from shotquill.capture.base import CaptureResult, DisplayInfo, Rect, ScreenCapturer, WindowInfo

if TYPE_CHECKING:
    from typing import BinaryIO

    from PySide6.QtGui import QImage

    from shotquill.ocr.base import TextRecognizer

# Largest image the headless surface will load into memory. Any real screenshot
# is far smaller; the cap stops a path to an enormous or unbounded file (e.g.
# ``/dev/zero``) from exhausting memory when an agent — the MCP surface's whole
# threat model — points OCR at it.
MAX_IMAGE_BYTES = 256 * 1024 * 1024

EXIT_PERMISSION = 3
EXIT_UNSUPPORTED = 4
EXIT_NO_MATCH = 5
EXIT_BLOCKED = 6
EXIT_INVALID_INPUT = 7


def printable(text: str) -> str:
    """Drop control characters from an app-supplied string before it is shown.

    A window's title and its owner (the WM_CLASS on X11, the executable name on
    Windows) are set by the owning app, as is any text OCR reads off the screen,
    so an untrusted/hostile source could embed ANSI escapes or other terminal
    control sequences that would hijack the terminal once the string is printed.
    Anything that reaches a human surface raw (the CLI table, an error message,
    an MCP text block) goes through here. JSON output is already escaped by
    ``json.dumps``. Normal text (including CJK and accents) is ``isprintable``
    and survives unchanged.
    """
    return "".join(c for c in text if c.isprintable())


class HeadlessError(Exception):
    """Base for typed headless failures; ``exit_code`` is the CLI contract."""

    exit_code = 1


class CapturePermissionError(HeadlessError):
    exit_code = EXIT_PERMISSION


class CapabilityUnsupported(HeadlessError):
    """The capability does not exist on this platform/session (e.g. listing
    windows under Wayland) — distinct from a transient failure, so agents can
    stop retrying and pick another path."""

    exit_code = EXIT_UNSUPPORTED

    def __init__(self, capability: str, reason: str) -> None:
        super().__init__(f"{capability} is not available: {reason}")
        self.capability = capability
        self.reason = reason


class WindowNotFound(HeadlessError):
    exit_code = EXIT_NO_MATCH


class DisplayNotFound(HeadlessError):
    """The requested display index does not exist (monitors may have been
    unplugged since the caller enumerated). Shares the no-match exit code:
    agents treat both as "re-list, then re-pick"."""

    exit_code = EXIT_NO_MATCH


class CaptureBlocked(HeadlessError):
    """The capture targets an app on the blocklist (or the blocklist is
    unreadable, which fails closed). Refusing is the point — this is the
    privacy feature working, not an error to retry."""

    exit_code = EXIT_BLOCKED


class ImageInputTooLarge(HeadlessError):
    """The image the caller handed in (file or stdin) is past ``MAX_IMAGE_BYTES``.

    Typed so it carries the documented invalid-input exit code instead of
    falling through to the generic catch-all — the size cap is a threat-model
    control (an agent pointing OCR at an unbounded source), so its breach gets a
    stable code agents can branch on."""

    exit_code = EXIT_INVALID_INPUT


def read_image_bytes(stream: BinaryIO, *, label: str) -> bytes:
    """Read an image from ``stream``, refusing inputs past ``MAX_IMAGE_BYTES``.

    Reading one byte past the cap distinguishes "exactly at the limit" from
    "over it" without trusting a stat (a pipe or ``/dev/zero`` reports no size),
    so an unbounded source can't OOM the process before the check fires.
    """
    data = stream.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        limit_mib = MAX_IMAGE_BYTES // (1024 * 1024)
        raise ImageInputTooLarge(f"{label} is larger than the {limit_mib} MiB image limit")
    return data


def decode_qimage(data: bytes, *, label: str):
    """Decode image bytes into a QImage, raising ``ValueError`` if undecodable.

    The one place the ``QImage.fromData`` + ``isNull`` check lives, so the CLI
    (`ocr`/`diff`) and MCP (`ocr`/`diff`) image readers can't drift on what counts
    as a decodable image or how the failure reads."""
    from PySide6.QtGui import QImage

    image = QImage.fromData(data)
    if image.isNull():
        raise ValueError(f"{label} is not a decodable image")
    return image


def render_recorded_frame(
    *,
    window_id=None,
    app=None,
    title=None,
    region=None,
    display=None,
    masks=(),
    reveal=(),
    redact_recognizer=None,
    max_dimension: int = 0,
):
    """The shared `session frame` pixel pipeline for both the CLI and MCP.

    Single-sources the *security-sensitive ordering* so the two front-ends can't
    drift on it: blocklist-enforced capture (``via="record"``) → caller masks →
    PII redaction → reveal mosaic → long-edge downscale → deterministic PNG
    encode. Returns ``(image, image_bytes, target, matched, blocklist)``; the
    caller still does its own recognize / assert / scan / record_frame and the
    surface-specific ambiguous-match report.
    """
    from shotquill.imaging import downscale_to_max, pixelate_except, result_to_qimage

    blocklist = active_blocklist()
    capturer = get_capturer()
    result, target, matched = perform_capture(
        capturer,
        window_id=window_id,
        app=app,
        title=title,
        region=region,
        display=display,
        blocklist=blocklist,
        via="record",
    )
    # Caller masks/reveal apply before OCR too, so a hidden field is also hidden
    # from the assertion, not just the archived frame.
    result = apply_masks(result, masks)
    # Mask likely PII before the frame is filed (and before the assert/scan OCR),
    # so the redacted pixels are what gets archived, asserted, and scanned.
    if redact_recognizer is not None:
        result = redact_pii(result, redact_recognizer)
    image = pixelate_except(result_to_qimage(result), reveal, result.scale)
    # Cap the long edge before OCR/encoding so the archived frame and the
    # assertion read the very same (possibly shrunk) pixels (cost control).
    image = downscale_to_max(image, max_dimension)
    # Deterministic encoding (pinned DPI, no volatile PNG chunks) so an unchanged
    # screen encodes byte-for-byte the same and `dedup` can spot it.
    image_bytes = encode_qimage(image, "png", deterministic=True)
    return image, image_bytes, target, matched, blocklist


def get_capturer(include_cursor: bool = False) -> ScreenCapturer:
    """Pick the platform capture backend (the CLI/MCP factory seam)."""
    if sys.platform == "darwin":
        from shotquill.capture.macos import MacScreenCapturer

        return MacScreenCapturer(include_cursor=include_cursor)
    if sys.platform.startswith("linux"):
        if _is_wayland_session():
            from shotquill.capture.wayland import PortalScreenCapturer

            return PortalScreenCapturer(include_cursor=include_cursor)
        from shotquill.capture.qtgrab import QtGrabCapturer

        return QtGrabCapturer(include_cursor=include_cursor)
    if sys.platform.startswith("win"):
        # Windows places no out-of-band grab restriction (unlike Wayland), so
        # the PySide6 ``QScreen.grabWindow`` path covers full-screen / region
        # capture with no extra dependency; the Windows subclass adds window
        # enumeration and by-id capture over the Win32 API.
        from shotquill.capture.windows import WindowsScreenCapturer

        return WindowsScreenCapturer(include_cursor=include_cursor)
    raise CapabilityUnsupported("capture", f"no backend for platform {sys.platform!r}")


def _is_wayland_session() -> bool:
    """True on a real Wayland desktop, where out-of-band grabs are refused and
    capture must go through xdg-desktop-portal instead of QScreen.grabWindow.

    An explicit ``QT_QPA_PLATFORM`` (e.g. ``offscreen`` in tests, or a forced
    X11/xcb) wins, so the Qt-grab path stays selectable without a live portal."""
    import os

    if os.environ.get("QT_QPA_PLATFORM"):
        return False
    return os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(
        os.environ.get("WAYLAND_DISPLAY")
    )


def get_recognizer() -> TextRecognizer:
    if sys.platform == "darwin":
        from shotquill.ocr.macos import VisionTextRecognizer

        return VisionTextRecognizer()
    if sys.platform.startswith("linux"):
        from shotquill.ocr.linux import TesseractTextRecognizer, tesseract_path

        if tesseract_path() is not None:
            return TesseractTextRecognizer()
        raise CapabilityUnsupported(
            "ocr", "Tesseract is not installed (install the 'tesseract-ocr' package)"
        )
    if sys.platform.startswith("win"):
        from shotquill.ocr import windows

        if windows.is_available():
            return windows.WindowsOcrRecognizer()
        raise CapabilityUnsupported(
            "ocr",
            "Windows OCR needs the WinRT runtime; install it with "
            "`pip install shotquill[windows-ocr]`",
        )
    raise CapabilityUnsupported("ocr", f"no OCR backend for platform {sys.platform!r}")


def select_window(
    windows: list[WindowInfo], app: str, title: str | None = None
) -> tuple[WindowInfo, int]:
    """Pick a window by case-insensitive substring on owner (and title).

    Returns the front-most match plus the total match count — ``list_windows``
    is contractually front-most-first, and capture is read-only/retryable, so
    on ambiguity we take the front window and let the caller warn rather than
    hard-fail (a hard error would cancel out the one-step convenience).
    """
    app_needle = app.casefold()
    title_needle = title.casefold() if title else None
    matches = [
        w
        for w in windows
        if app_needle in w.owner.casefold()
        and (title_needle is None or title_needle in w.title.casefold())
    ]
    if not matches:
        wanted = f"app {app!r}" + (f" title {title!r}" if title else "")
        raise WindowNotFound(f"no on-screen window matches {wanted}")
    return matches[0], len(matches)


def select_display(displays: list[DisplayInfo], index: int) -> DisplayInfo:
    """Pick a display by its enumeration index (primary is 0)."""
    if index < 0:
        raise DisplayNotFound(f"display index must be >= 0 — got {index}")
    for display in displays:
        if display.index == index:
            return display
    raise DisplayNotFound(
        f"no display {index}: this machine has {len(displays)} "
        f"(0..{len(displays) - 1}; see `squill display list`)"
    )


def active_blocklist():
    """Load the user's blocklist, failing closed when it cannot be read.

    A missing file is the empty list (the common case, no friction). A
    present-but-corrupt file means the user opted into protection that is now
    broken, so we refuse to capture rather than silently grab something they
    meant to block.
    """
    from shotquill import blocklist as bl

    try:
        return bl.load()
    except bl.BlocklistError as exc:
        raise CaptureBlocked(f"blocklist is unreadable, refusing to capture: {exc}") from exc


def _refuse_if_blocked(window: WindowInfo, blocklist, *, via: str) -> None:
    """Raise :class:`CaptureBlocked` (and audit it) if a rule blocks ``window``."""
    rule = blocklist.match(window)
    if rule is None:
        return
    target = f"{window.owner} — {window.title}" if window.title else window.owner
    from shotquill import audit

    audit.record("capture_blocked", via=via, target=target)
    # The owner (WM_CLASS on X11) is app-controlled and this message is printed
    # raw to the terminal by the CLI, so strip control chars to keep a hostile
    # blocklisted app from smuggling ANSI escapes through the refusal.
    raise CaptureBlocked(
        f"{printable(window.owner)} is on the app blocklist "
        f"(rule {rule.describe()}); refusing to capture it"
    )


def active_allowlist():
    """Load the user's capture allowlist, failing closed when it cannot be read.

    A missing file is the disabled empty list (the default — no friction). A
    present-but-corrupt file while the leash is meant to be on means we cannot
    tell what is permitted, so we refuse to capture rather than guess.
    """
    from shotquill import allowlist as al

    try:
        return al.load()
    except al.AllowlistError as exc:
        raise CaptureBlocked(f"allowlist is unreadable, refusing to capture: {exc}") from exc


def _refuse_if_not_allowed(window: WindowInfo, allowlist, *, via: str) -> None:
    """Raise :class:`CaptureBlocked` (and audit it) unless ``window`` is allowed.

    Only called when the allowlist is enforcing; the default (disabled) list
    never reaches here, so an ordinary capture is untouched.
    """
    if allowlist.is_allowed(window):
        return
    target = f"{window.owner} — {window.title}" if window.title else window.owner
    from shotquill import audit

    audit.record("capture_not_allowed", via=via, target=target)
    # The owner is app-controlled and printed raw by the CLI — strip control
    # chars, exactly as the blocklist refusal does.
    raise CaptureBlocked(
        f"{printable(window.owner)} is not on the capture allowlist; refusing to "
        "capture it (the allowlist is active, so only listed apps may be captured)"
    )


def _enforce_allowlist_window(allowlist, target: WindowInfo | None, window_id: int, *, via: str):
    """Apply the enforcing allowlist to a by-id capture, failing closed.

    The allowlist permits by *identity*, so we must positively confirm what this
    id is. When it cannot be identified — enumeration unavailable on this backend,
    or the id is not among the enumerable windows — we refuse rather than capture
    something we could not check against the list.
    """
    if target is None:
        from shotquill import audit

        audit.record("capture_not_allowed", via=via, target=f"window {window_id}")
        raise CaptureBlocked(
            f"the capture allowlist is active but window {window_id} cannot be "
            "identified to check it against the list on this backend; refusing to capture"
        )
    _refuse_if_not_allowed(target, allowlist, via=via)


def _refuse_whole_screen_under_allowlist(kind: str, *, via: str) -> None:
    """Refuse a fullscreen / region / display capture while the allowlist enforces.

    A whole-screen grab captures everything, which the allowlist's "only these
    apps" contract cannot honour, so it is refused outright (cross-platform, no
    enumeration needed) — the caller must target a specific window or app.
    """
    from shotquill import audit

    audit.record("capture_not_allowed", via=via, target=kind)
    raise CaptureBlocked(
        f"the capture allowlist is active: only listed apps may be captured, so a "
        f"{kind} capture is refused — capture a specific window (--window-id) or app "
        "(--app) instead"
    )


def _refuse_whole_screen_under_blocklist(kind: str, *, via: str) -> None:
    """Refuse a capture whose pixels cannot be checked against the blocklist.

    Whole-screen/region/display/interactive frames may contain any app, and an
    unresolved window id may be a blocked app. If the backend cannot enumerate
    windows to verify and redact, continuing would violate the blocklist's
    "never capture these apps" contract, so all front-ends fail closed.
    """
    from shotquill import audit

    audit.record("capture_blocked", via=via, target=kind)
    raise CaptureBlocked(
        f"the app blocklist is active, but {kind} capture cannot be checked or "
        "redacted on this desktop; refusing to capture"
    )


def _require_blocklist_enumeration(
    capturer: ScreenCapturer, kind: str, *, via: str
) -> list[WindowInfo]:
    """Fail before grabbing pixels when a blocklisted whole-frame path cannot
    enumerate windows for redaction."""
    try:
        return capturer.list_windows()
    except CapabilityUnsupported:
        _refuse_whole_screen_under_blocklist(kind, via=via)


def perform_interactive_capture(
    capturer: ScreenCapturer, *, blocklist=None, allowlist=None, via: str = "cli"
) -> tuple[CaptureResult, str, int]:
    """Drive an interactive (compositor-picked) capture, honouring capture policy.

    The picker may land on any window or the whole screen, so — exactly like a
    fullscreen / region / display grab — an enforcing allowlist refuses it: its
    "only these apps" contract cannot be honoured for a user-framed selection.
    Keeping the gate here (not in the CLI) means every front-end and the audit
    log get the same policy the other whole-screen paths already do.

    A blocklist also refuses it: the backend hands back an already-composited
    selection and Wayland cannot enumerate windows to prove no blocked app is in
    it. Failing closed keeps the blocklist's "never capture these apps" promise
    consistent across GUI, CLI, and MCP.

    Returns ``(result, "interactive", 1)`` to match :func:`perform_capture`.
    """
    if blocklist is None:
        blocklist = active_blocklist()
    if allowlist is None:
        allowlist = active_allowlist()
    if blocklist:
        _refuse_whole_screen_under_blocklist("interactive", via=via)
    if allowlist:
        _refuse_whole_screen_under_allowlist("interactive", via=via)
    return capturer.capture_interactive(), "interactive", 1


def perform_capture(
    capturer: ScreenCapturer,
    *,
    window_id: int | None = None,
    app: str | None = None,
    title: str | None = None,
    region: Rect | None = None,
    display: int | None = None,
    blocklist=None,
    allowlist=None,
    via: str = "cli",
) -> tuple[CaptureResult, str, int]:
    """Dispatch one capture and describe what was actually hit.

    Returns ``(result, target, matched)`` where ``target`` names the real
    capture subject (the audit log records truth, not the request) and
    ``matched`` is the ambiguity count for app/title selection (always 1
    otherwise) so front-ends can warn their own way.

    A window or app capture that lands on the blocklist raises
    :class:`CaptureBlocked`. An empty blocklist (the default) takes the exact
    same path as before — no extra window enumeration, no new failure modes.
    Full-screen, display and region captures redact blocked windows when the
    backend can enumerate them; when it cannot, they fail closed rather than
    returning pixels that may contain a blocked app.

    When the **allowlist** is enabled it inverts that default: a window/app
    capture is refused unless the target is on the list, and a whole-screen
    capture (fullscreen, region, display) is refused outright — its "only these
    apps" contract cannot be honoured for a grab of everything. A disabled
    allowlist (the default) is inert and changes nothing.
    """
    if blocklist is None:
        blocklist = active_blocklist()
    if allowlist is None:
        allowlist = active_allowlist()

    if window_id is not None:
        target, windows = _lookup_window(
            capturer, window_id, need_enum=bool(blocklist) or bool(allowlist)
        )
        if target is not None:
            _refuse_if_blocked(target, blocklist, via=via)
        elif blocklist:
            # The blocklist denies by identity, so an id we cannot resolve to a
            # window (enumeration unavailable on this backend, or the id is not
            # among the enumerable windows) cannot be checked against it. Fail
            # closed: a blocked app must never pass through merely because this
            # backend cannot tell us what the id belongs to.
            _refuse_whole_screen_under_blocklist(f"window {window_id}", via=via)
        if allowlist:
            _enforce_allowlist_window(allowlist, target, window_id, via=via)
        result = capturer.capture_window(window_id)
        if target is not None and (blocklist or allowlist):
            result = _redact_window_overlaps(
                result, capturer, target, windows, blocklist, allowlist, via=via
            )
        return result, f"window {window_id}", 1
    if app:
        windows = capturer.list_windows()
        window, matched = select_window(windows, app, title)
        if blocklist:
            _refuse_if_blocked(window, blocklist, via=via)
        if allowlist:
            _refuse_if_not_allowed(window, allowlist, via=via)
        result = capturer.capture_window(window.window_id)
        if blocklist or allowlist:
            result = _redact_window_overlaps(
                result, capturer, window, windows, blocklist, allowlist, via=via
            )
        return result, f"{window.owner} — {window.title}", matched
    # Only whole-screen captures (fullscreen, region, display) remain. The
    # allowlist refuses them all; capture a specific window or app instead.
    if allowlist:
        if display is not None:
            kind = "display"
        elif region is not None:
            kind = "region"
        else:
            kind = "fullscreen"
        _refuse_whole_screen_under_allowlist(kind, via=via)
    if display is not None:
        # One display is a rectangle of the virtual desktop, so this rides the
        # region path — including its blocklist redaction — instead of growing
        # a parallel capture mode per backend.
        picked = select_display(capturer.list_displays(), display)
        bounds = picked.bounds
        target = f"display {picked.index} ({bounds.width}x{bounds.height} at {bounds.x},{bounds.y})"
        windows = None
        if blocklist:
            windows = _require_blocklist_enumeration(capturer, target, via=via)
        try:
            result = capturer.capture_region(bounds)
        except ValueError as exc:
            # The index was valid at enumeration time, so bounds the backend now
            # rejects mean the display changed under us (unplugged, or a Wayland
            # frame that doesn't cover it). That is a re-list-and-re-pick signal
            # for the caller, not invalid arguments and not a generic failure.
            raise DisplayNotFound(f"{target} is no longer capturable: {exc}") from exc
        if blocklist:
            result = _redact_blocked(
                result,
                capturer,
                blocklist,
                origin=(bounds.x, bounds.y),
                target=target,
                via=via,
                windows=windows,
            )
        return result, target, 1
    if region is not None:
        target = f"region {region.x},{region.y},{region.width},{region.height}"
        windows = None
        if blocklist:
            windows = _require_blocklist_enumeration(capturer, target, via=via)
        result = capturer.capture_region(region)
        if blocklist:
            result = _redact_blocked(
                result,
                capturer,
                blocklist,
                origin=(region.x, region.y),
                target=target,
                via=via,
                windows=windows,
            )
        return result, target, 1
    if not blocklist:
        return capturer.capture_fullscreen(), "fullscreen", 1
    return _fullscreen_with_blocklist(capturer, blocklist, via=via), "fullscreen", 1


def _fullscreen_with_blocklist(capturer: ScreenCapturer, blocklist, *, via: str) -> CaptureResult:
    """Full-screen capture with blocklisted windows kept out of the image.

    Where the backend can omit them at capture time (macOS ScreenCaptureKit)
    they are simply absent — windows on top stay intact and nothing is painted.
    Anything the capture could not exclude (the legacy path) is then redacted by
    solid block as a fallback. Where windows cannot be enumerated at all (e.g.
    Wayland) the frame cannot be protected, so it is refused.
    """
    from shotquill import audit, redact

    try:
        windows = capturer.list_windows()
    except CapabilityUnsupported:
        _refuse_whole_screen_under_blocklist("fullscreen", via=via)
    blocked = blocklist.blocked(windows)
    if not blocked:
        return capturer.capture_fullscreen()

    blocked_ids = frozenset(w.window_id for w in blocked)
    result = capturer.capture_fullscreen(exclude_window_ids=blocked_ids)
    # Solid-block any blocklisted window the capture could not omit itself; the
    # origin/scale it reports map logical bounds onto the right pixels even on a
    # multi-monitor desktop whose top-left is not (0, 0).
    remaining = [w for w in blocked if w.window_id not in result.excluded_window_ids]
    if remaining:
        result, _ = redact.redact_bounds(
            result, (result.origin_x, result.origin_y), [w.bounds for w in remaining]
        )
    labels = ", ".join(w.bundle_id or w.owner for w in blocked)
    audit.record("capture_redacted", via=via, target=f"fullscreen [hidden: {labels}]")
    return result


def _redact_blocked(
    result: CaptureResult,
    capturer: ScreenCapturer,
    blocklist,
    *,
    origin: tuple[int, int],
    target: str,
    via: str,
    windows: list[WindowInfo] | None = None,
) -> CaptureResult:
    """Paint solid blocks over any blocklisted window inside ``result`` (the
    region path, which cannot omit windows at capture time).

    Enumeration is required to find the sensitive windows; where it is
    unavailable (e.g. Wayland) the frame cannot be protected, so it is refused
    rather than returned plainly.
    """
    from shotquill import audit, redact

    if windows is None:
        try:
            windows = capturer.list_windows()
        except CapabilityUnsupported:
            _refuse_whole_screen_under_blocklist(target, via=via)
    blocked = blocklist.blocked(windows)
    if not blocked:
        return result
    redacted, count = redact.redact_bounds(result, origin, [w.bounds for w in blocked])
    if count:
        labels = ", ".join(w.bundle_id or w.owner for w in blocked)
        audit.record("capture_redacted", via=via, target=f"{target} [redacted: {labels}]")
    return redacted


def _lookup_window(
    capturer: ScreenCapturer, window_id: int, *, need_enum: bool
) -> tuple[WindowInfo | None, list[WindowInfo] | None]:
    """Enumerate windows to find the by-id target, for the blocklist/allowlist checks.

    Returns ``(target, windows)`` — ``(None, None)`` when neither list is
    enforcing (nothing to match against) or enumeration is unavailable, so the
    capture proceeds exactly as before."""
    if not need_enum:
        return None, None
    try:
        windows = capturer.list_windows()
    except CapabilityUnsupported:
        return None, None
    target = next((w for w in windows if w.window_id == window_id), None)
    return target, windows


def _overlap_must_hide(window: WindowInfo, blocklist, allowlist) -> bool:
    """Whether ``window``'s pixels must not leak into a capture of another window.

    True when the window is blocklisted, or — when the allowlist is enforcing —
    not on it. Both lists protect the *captured image*: a blocklisted app must
    never appear, and under an allowlist only allowed apps may, so an unrelated
    window stacked over the target is a leak either way.
    """
    if blocklist.match(window) is not None:
        return True
    return bool(allowlist) and not allowlist.is_allowed(window)


def _redact_window_overlaps(
    result: CaptureResult,
    capturer: ScreenCapturer,
    target: WindowInfo,
    windows: list[WindowInfo] | None,
    blocklist,
    allowlist,
    *,
    via: str,
) -> CaptureResult:
    """Solid-block any window overlapping ``target`` whose pixels must not leak,
    when this backend's window grab may include pixels stacked *above* it.

    "Must not leak" means blocklisted, or — with the allowlist on — not allowed:
    capturing an allowed window must not smuggle in a non-allowed one that sits
    over it. Only the no-compositor X11 framebuffer read captures an overlapping
    window's pixels; surface-accurate backends (macOS ScreenCaptureKit, Windows,
    X11 with a compositor) grab the target's own surface, so this is a no-op for
    them and never paints a false block over the legitimate capture. The
    capability is read defensively so a duck-typed capturer that predates it
    counts as surface-accurate.

    X11 stacking order is not always available (the EWMH client-list fallback
    carries none), so *every* overlapping hidden window is redacted rather than
    only those provably above the target — over-covering a sliver is the safe
    direction; leaking a password manager (or a non-allowlisted app) is not.
    ``target.bounds`` (logical points) is the redaction origin, matching the
    region path, and ``result.scale`` maps those points onto the captured pixels.
    """
    if windows is None:
        return result
    includes_overlaps = getattr(capturer, "window_capture_includes_overlaps", None)
    if includes_overlaps is None or not includes_overlaps():
        return result
    from shotquill import audit, redact

    overlaps = [
        w.bounds
        for w in windows
        if w.window_id != target.window_id
        and _overlap_must_hide(w, blocklist, allowlist)
        and redact.rect_intersects(target.bounds, w.bounds)
    ]
    if not overlaps:
        return result
    result, _ = redact.redact_bounds(result, (target.bounds.x, target.bounds.y), overlaps)
    audit.record("capture_redacted", via=via, target=f"window {target.window_id} [overlap hidden]")
    return result


def apply_masks(result: CaptureResult, masks: list[Rect]) -> CaptureResult:
    """Paint solid blocks over caller-supplied rectangles before the frame is
    used anywhere — the dynamic, caller-controlled redaction layer.

    Coordinates are **image-relative logical points**: ``(0, 0)`` is the frame's
    own top-left, so a caller masks a rectangle within the screenshot it asked
    for without needing to know where on the virtual desktop it was captured.
    Reuses the same hardened fill path as the blocklist, and stacks on top of it
    (the blocklist already ran inside ``perform_capture``). Returns the input
    unchanged when there are no masks.
    """
    if not masks:
        return result
    from shotquill import redact

    masked, _ = redact.redact_bounds(result, (0, 0), masks)
    return masked


def redact_pii(result: CaptureResult, recognizer: TextRecognizer) -> CaptureResult:
    """Mask the pixels of any likely-PII text in ``result`` (best-effort).

    The content-level redaction layer: OCR the frame, find which recognized boxes
    carry likely PII, and fill those pixel rectangles with the same hardened path
    the blocklist and caller masks use — so a card number or email is gone from
    the bytes, not just flagged. Best-effort, **not a guarantee**: it can only
    mask what OCR reads and the detectors catch. Returns the input unchanged when
    nothing is flagged.
    """
    from shotquill import pii, redact
    from shotquill.imaging import result_to_qimage

    rects = pii.redaction_rects(recognizer.recognize_boxes(result_to_qimage(result)))
    return redact.fill_rects(result, rects) if rects else result


def compute_pair_diffs(session, *, threshold: int = 16) -> dict[int, dict]:
    """Diff each before/after pair's filed images; return ``{frame_position: box}``.

    Qt glue for the record-end path: loads the on-disk frame images, computes the
    changed region of every before/after pair (``imaging.frame_diff_fraction``),
    and returns each box — fractions of the frame — keyed by the *after* frame's
    0-based position in the manifest. Best-effort: an unreadable image, a size
    mismatch, or no change simply omits that pair.
    """
    from PySide6.QtGui import QImage

    from shotquill import imaging, record

    frames = record.load_manifest(session).get("frames", [])
    before_image: dict[str, str] = {}
    diffs: dict[int, dict] = {}
    for pos, entry in enumerate(frames):
        pair_id, phase = entry.get("pair_id"), entry.get("phase")
        if pair_id and phase == record.PHASE_BEFORE:
            before_image[pair_id] = entry.get("image", "")
        elif pair_id and phase == record.PHASE_AFTER and pair_id in before_image:
            before = QImage(str(session.dir / before_image[pair_id]))
            after = QImage(str(session.dir / entry.get("image", "")))
            if before.isNull() or after.isNull():
                continue
            frac = imaging.frame_diff_fraction(before, after, threshold=threshold)
            if frac is not None:
                diffs[pos] = {"x": frac[0], "y": frac[1], "width": frac[2], "height": frac[3]}
    return diffs


def annotate_pair_diffs(session) -> None:
    """Compute before/after change boxes and store them on the session's frames."""
    from shotquill import record

    record.attach_diffs(session, compute_pair_diffs(session))


def windows_payload(windows: list[WindowInfo]) -> list[dict]:
    """The machine-readable window list shared by ``--json`` and MCP."""
    return [
        {
            "id": w.window_id,
            "owner": w.owner,
            "title": w.title,
            "bundle_id": w.bundle_id,
            "bounds": {
                "x": w.bounds.x,
                "y": w.bounds.y,
                "width": w.bounds.width,
                "height": w.bounds.height,
            },
        }
        for w in windows
    ]


def displays_payload(displays: list[DisplayInfo]) -> list[dict]:
    """The machine-readable display list shared by ``--json`` and MCP."""
    return [
        {
            "index": d.index,
            "name": d.name,
            "primary": d.primary,
            "scale": d.scale,
            "bounds": {
                "x": d.bounds.x,
                "y": d.bounds.y,
                "width": d.bounds.width,
                "height": d.bounds.height,
            },
        }
        for d in displays
    ]


def parse_region(text: str) -> Rect:
    """Parse the ``x,y,w,h`` syntax (four integers, logical coordinates)."""
    parts = text.split(",")
    if len(parts) != 4:
        raise ValueError(f"region must be x,y,w,h — got {text!r}")
    try:
        x, y, w, h = (int(p.strip()) for p in parts)
    except ValueError:
        raise ValueError(f"region must be four integers x,y,w,h — got {text!r}") from None
    if w <= 0 or h <= 0:
        raise ValueError(f"region width/height must be positive — got {text!r}")
    return Rect(x=x, y=y, width=w, height=h)


def downscale_to_width(image: QImage, max_width: int) -> QImage:
    """Cap the width (keeping aspect), shared by ``--max-width`` and MCP.

    A smaller image is returned untouched — the option means "at most",
    so callers can pass a constant without checking the screen size first.
    """
    if max_width <= 0:
        raise ValueError("max_width must be positive")
    if image.width() <= max_width:
        return image
    from PySide6.QtCore import Qt

    return image.scaledToWidth(max_width, Qt.TransformationMode.SmoothTransformation)


def encode_qimage(
    image: QImage, image_format: str = "png", *, deterministic: bool = False
) -> bytes:
    """Serialize a QImage to PNG/JPEG bytes (for ``-o -`` and MCP payloads).

    With ``deterministic`` the embedded resolution is pinned and PNG timestamp /
    text chunks are stripped, so the same pixels always encode to the same bytes
    regardless of the capturing display — what a golden-image or content-hash
    assertion needs. See :mod:`shotquill.deterministic`.
    """
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QImage as _QImage

    fmt = "jpg" if image_format.lower() in ("jpg", "jpeg") else "png"
    if deterministic:
        from shotquill import deterministic as det

        image = det.normalize_image(image)
    if fmt == "jpg":
        # JPEG has no alpha; convert explicitly so the result is deterministic.
        image = image.convertToFormat(_QImage.Format.Format_RGB888)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, fmt.upper()):
        raise OSError(f"failed to encode image as {fmt}")
    encoded = bytes(data)
    if deterministic:
        from shotquill import deterministic as det

        encoded = det.strip_volatile_png_chunks(encoded)
    return encoded


def doctor_checks() -> list[dict]:
    """The capability matrix behind ``squill doctor`` and the MCP tool."""
    import platform

    from shotquill import paths

    platform_detail = f"{sys.platform} / Python {platform.python_version()}"
    if sys.platform.startswith("linux"):
        # X11 vs Wayland vs a bare tty decides which capabilities can work,
        # so surface it where troubleshooting starts.
        import os

        platform_detail += f" / session {os.environ.get('XDG_SESSION_TYPE') or 'unknown'}"

    checks: list[dict] = [
        {"capability": "platform", "available": True, "detail": platform_detail},
        {"capability": "audit_log", "available": True, "detail": str(paths.audit_log_path())},
        _check_blocklist(),
        _check_allowlist(),
    ]

    capture_checks, can_enumerate = _capture_checks()
    checks.extend(capture_checks)
    # Without window enumeration (Linux today) a full-screen capture can't locate
    # a blocked app to redact it, so the blocklist silently does NOT protect those
    # grabs — surface that where there are rules meant to.
    redaction = _check_blocklist_redaction(can_enumerate)
    if redaction is not None:
        checks.append(redaction)

    checks.append(_check_hotkeys())

    if sys.platform == "darwin":
        checks.append(_check_screen_recording())

    try:
        recognizer = get_recognizer()
        checks.append({"capability": "ocr", "available": True, "detail": recognizer.backend_name})
    except CapabilityUnsupported as exc:
        checks.append({"capability": "ocr", "available": False, "detail": exc.reason})

    return checks


def _check_hotkeys() -> dict:
    """Whether global capture hotkeys can be grabbed on this session.

    Mirrors the capture probe — report what is actually *reachable*, not merely
    that a backend exists. On Wayland out-of-band key grabs are refused, so the
    hotkeys go through the xdg-desktop-portal GlobalShortcuts interface, which a
    minimal desktop may not ship; surface that as the actionable thing to fix. On
    X11 pynput grabs keys without a grant; on macOS it needs Input Monitoring
    (a runtime prompt, so reported best-effort)."""
    if sys.platform == "darwin":
        return {
            "capability": "hotkeys",
            "available": True,
            "detail": "pynput (needs the Input Monitoring permission)",
        }
    if sys.platform.startswith("linux"):
        if _is_wayland_session():
            from shotquill.hotkeys.wayland import globalshortcuts_available

            if globalshortcuts_available():
                return {
                    "capability": "hotkeys",
                    "available": True,
                    "detail": "xdg-desktop-portal GlobalShortcuts reachable",
                }
            return {
                "capability": "hotkeys",
                "available": False,
                "detail": "GlobalShortcuts portal unreachable; install xdg-desktop-portal "
                "(or bind a compositor shortcut to `squill capture`)",
            }
        return {"capability": "hotkeys", "available": True, "detail": "pynput (X11)"}
    return {
        "capability": "hotkeys",
        "available": False,
        "detail": f"no hotkey backend for {sys.platform}",
    }


def _capture_checks() -> tuple[list[dict], bool | None]:
    """The ``capture`` + ``list_windows`` checks, and whether windows can be
    enumerated (``None`` when capture itself is unavailable).

    On Wayland the backend is xdg-desktop-portal, which is only useful if the
    portal is actually installed and running (a minimal desktop may lack it), so
    this probes reachability instead of reporting capture as available merely
    because the backend class instantiated."""
    try:
        capturer = get_capturer()
    except CapabilityUnsupported as exc:
        return [
            {"capability": "capture", "available": False, "detail": exc.reason},
            {"capability": "list_windows", "available": False, "detail": exc.reason},
        ], None

    detail, available = _capture_detail(type(capturer).__name__)
    checks = [{"capability": "capture", "available": available, "detail": detail}]
    checks.append(_check_displays(capturer))
    try:
        capturer.list_windows()
        checks.append(
            {"capability": "list_windows", "available": True, "detail": type(capturer).__name__}
        )
        return checks, True
    except CapabilityUnsupported as exc:
        checks.append({"capability": "list_windows", "available": False, "detail": exc.reason})
        return checks, False


def _check_displays(capturer: ScreenCapturer) -> dict:
    """How many monitors `capture --display N` can pick from, with geometry —
    the doctor is where agents learn the valid indexes without a capture."""
    try:
        displays = capturer.list_displays()
    except CapabilityUnsupported as exc:
        return {"capability": "displays", "available": False, "detail": exc.reason}
    except Exception as exc:  # noqa: BLE001 - the doctor reports problems, it must not crash on one
        return {"capability": "displays", "available": False, "detail": f"probe: {exc}"}
    described = ", ".join(
        f"{d.index}: {d.bounds.width}x{d.bounds.height} at {d.bounds.x},{d.bounds.y}"
        + (" (primary)" if d.primary else "")
        for d in displays
    )
    return {"capability": "displays", "available": True, "detail": described}


def _capture_detail(backend: str) -> tuple[str, bool]:
    """Detail string and availability for the ``capture`` check.

    On a real Wayland session the grab goes through xdg-desktop-portal; probe
    that it is reachable and, if not, hand back an actionable hint rather than a
    bare backend name that hides why captures will fail."""
    if sys.platform.startswith("linux") and _is_wayland_session():
        from shotquill.capture.wayland import portal_available

        if portal_available():
            return "xdg-desktop-portal (Wayland)", True
        return (
            "xdg-desktop-portal not reachable — install xdg-desktop-portal and a "
            "backend for your desktop (e.g. xdg-desktop-portal-gnome / -kde / -wlr)",
            False,
        )
    return backend, True


def _check_blocklist_redaction(can_enumerate: bool | None) -> dict | None:
    """Whether the blocklist can actually redact full-screen captures.

    Returns ``None`` when there is nothing to report — no rules, an unreadable
    list (already flagged by :func:`_check_blocklist`), or capture unavailable.
    Redaction needs to enumerate windows to find a blocked app's bounds, so
    where enumeration is unsupported, blocklist-protected full-screen captures
    are refused rather than returned plainly."""
    if can_enumerate is None:
        return None
    from shotquill import blocklist as bl
    from shotquill import paths

    try:
        loaded = bl.load(paths.blocklist_path())
    except bl.BlocklistError:
        return None  # the app_blocklist check already reports the corrupt file
    if not loaded:
        return None  # no rules → nothing to protect
    if can_enumerate:
        return {
            "capability": "blocklist_redaction",
            "available": True,
            "detail": "blocked windows are redacted from full-screen/region captures",
        }
    return {
        "capability": "blocklist_redaction",
        "available": False,
        "detail": (
            "no window enumeration on this backend → blocklist-protected full-screen "
            "captures are refused rather than captured plainly"
        ),
    }


def _check_blocklist() -> dict:
    """Report the app blocklist so users can confirm what is protected — and
    catch a corrupt file, which fails closed and would otherwise only surface
    as refused captures."""
    from shotquill import blocklist as bl
    from shotquill import paths

    path = paths.blocklist_path()
    try:
        loaded = bl.load(path)
    except bl.BlocklistError as exc:
        return {"capability": "app_blocklist", "available": False, "detail": f"{path}: {exc}"}
    if not loaded:
        detail = f"no rules ({path})"
    else:
        detail = f"{len(loaded.rules)} rule(s): " + ", ".join(r.describe() for r in loaded.rules)
    return {"capability": "app_blocklist", "available": True, "detail": detail}


def _check_allowlist() -> dict:
    """Report the capture allowlist: whether it is enforcing and what it permits.

    A corrupt file fails closed (every capture refused) just like the blocklist,
    so surface it here. When enabled, an empty rule set permits nothing — a full
    lockdown the user should see spelled out rather than discover as blanket
    refusals."""
    from shotquill import allowlist as al
    from shotquill import paths

    path = paths.allowlist_path()
    try:
        loaded = al.load(path)
    except al.AllowlistError as exc:
        return {"capability": "app_allowlist", "available": False, "detail": f"{path}: {exc}"}
    if not loaded.enabled:
        return {"capability": "app_allowlist", "available": True, "detail": f"disabled ({path})"}
    if not loaded.rules:
        return {
            "capability": "app_allowlist",
            "available": False,
            "detail": "ENABLED with no rules → nothing can be captured (full lockdown)",
        }
    rules = ", ".join(r.describe() for r in loaded.rules)
    return {
        "capability": "app_allowlist",
        "available": True,
        "detail": f"ENABLED — only these may be captured ({len(loaded.rules)} rule(s)): {rules}",
    }


def screen_recording_detail(granted: bool, responsible: list[str]) -> str | None:
    """Build the ``screen_recording`` check detail (pure, so it is testable).

    When denied, name the **responsible process** — the parent macOS attributes
    Screen Recording to — because the #1 mistake is granting Terminal when the
    real controller is something else (an agent host, or in CI the runner agent
    that spawned ``squill``). A correct grant follows that process, not Terminal.
    Returns ``None`` when granted (nothing to report).

    Process names are app-influenced (argv[0]/``PR_SET_NAME``), so strip control
    characters before they reach the terminal — the same ANSI-injection guard the
    blocklist/allowlist hints apply to window titles.
    """
    if granted:
        return None
    names = [printable(name) for name in responsible]
    who = " ← ".join(names) if names else "the process that ran squill"
    return (
        "denied. macOS attributes Screen Recording to the responsible process — "
        f"grant it to that, not Terminal. Caller chain (nearest first): {who}. "
        "In CI/VMs grant it non-interactively (MDM PPPC profile, or a "
        "pre-authorized VM template); a GitHub-hosted runner can't. Settings pane: "
        "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
    )


def _check_screen_recording() -> dict:  # pragma: no cover - macOS only
    """TCC preflight: a denied grant fails silently at capture time, so the
    doctor surfaces it — and names which process to grant — instead of letting
    agents see black frames."""
    try:
        from Quartz import CGPreflightScreenCaptureAccess

        granted = bool(CGPreflightScreenCaptureAccess())
    except Exception as exc:
        return {"capability": "screen_recording", "available": False, "detail": f"probe: {exc}"}
    from shotquill import audit

    detail = screen_recording_detail(granted, audit.caller_chain())
    return {"capability": "screen_recording", "available": granted, "detail": detail}
