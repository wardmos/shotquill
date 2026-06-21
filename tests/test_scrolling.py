# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the long-screenshot (scrolling) capture path.

The headless loop is driven through its ``source`` injection point (a list of
frames standing in for live region grabs) so the stitching, settle, and height-cap
logic is exercised deterministically with no display, timer, or input synthesis.
The CLI tests cover the ``--scrolling`` flag's up-front validation, which short-
circuits before any capture.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import (  # noqa: E402
    QColor,
    QImage,
)

from shotquill import allowlist as al  # noqa: E402
from shotquill import blocklist as bl  # noqa: E402
from shotquill import cli, headless  # noqa: E402
from shotquill.capture.base import CaptureResult, Rect  # noqa: E402

REGION = Rect(0, 0, 10, 30)


class _DummyCapturer:
    """Stands in where the loop never calls the backend (frames are injected)."""

    include_cursor = False

    def capture_region(self, region):  # pragma: no cover - unreached with source=
        raise AssertionError("capture_region should not be called when source is injected")


def _row_color(seed: int) -> QColor:
    return QColor(seed % 251, (seed * 7) % 251, (seed * 13) % 251)


def _page(width: int, n_rows: int) -> QImage:
    img = QImage(width, n_rows, QImage.Format.Format_RGBA8888)
    for y in range(n_rows):
        c = _row_color(y)
        for x in range(width):
            img.setPixelColor(x, y, c)
    return img


def _crop(page: QImage, y: int, height: int) -> QImage:
    return page.copy(0, y, page.width(), height)


def _row_seed(img: QImage, y: int) -> tuple[int, int, int]:
    c = img.pixelColor(0, y)
    return (c.red(), c.green(), c.blue())


# --- perform_scrolling_capture -----------------------------------------------


def test_scrolling_stitches_injected_frames():
    page = _page(10, 100)
    frames = [_crop(page, off, 30) for off in (0, 10, 20)]  # 20-row overlap each
    image, target, count = headless.perform_scrolling_capture(
        _DummyCapturer(), REGION, allowlist=None, source=frames
    )
    assert count == 3
    assert (image.width(), image.height()) == (10, 50)  # 30 + 10 + 10
    for y in range(50):
        assert _row_seed(image, y) == _row_seed(page, y)
    assert target == "scrolling region 0,0,10,30"


def test_scrolling_stops_after_view_settles():
    page = _page(10, 100)
    a, b = _crop(page, 0, 30), _crop(page, 10, 30)
    # a → b is a real scroll; the trailing identical b's are the page sitting still.
    image, _target, count = headless.perform_scrolling_capture(
        _DummyCapturer(), REGION, allowlist=None, settle=2, source=[a, b, b, b]
    )
    assert count == 2  # the duplicate b's are dropped, not appended
    assert image.height() == 40  # 30 + one 10px step


def test_scrolling_single_frame_returns_that_frame():
    page = _page(10, 30)
    image, _target, count = headless.perform_scrolling_capture(
        _DummyCapturer(), REGION, allowlist=None, source=[_crop(page, 0, 30)]
    )
    assert count == 1
    assert image.height() == 30


def test_scrolling_caps_at_max_height():
    page = _page(10, 100)
    frames = [_crop(page, off, 30) for off in (0, 10, 20, 30, 40)]
    image, _target, _count = headless.perform_scrolling_capture(
        _DummyCapturer(), REGION, allowlist=None, max_height=45, source=frames
    )
    assert image.height() == 45  # grown past the cap, then cropped back to it


def test_scrolling_refused_when_allowlist_enabled():
    locked = al.Allowlist(enabled=True, rules=(bl.BlockRule(bundle_id="com.apple.Terminal"),))
    with pytest.raises(headless.CaptureBlocked) as exc:
        headless.perform_scrolling_capture(_DummyCapturer(), REGION, allowlist=locked, source=[])
    assert exc.value.exit_code == headless.EXIT_BLOCKED


# --- auto-scroll (synthetic wheel) -------------------------------------------


def _result(width: int, seeds) -> CaptureResult:
    px = bytearray()
    for s in seeds:
        c = _row_color(s)
        px += bytes([c.red(), c.green(), c.blue(), 255]) * width
    seeds = list(seeds)
    return CaptureResult(width=width, height=len(seeds), scale=1.0, pixels=bytes(px))


class _SequenceCapturer:
    """Returns the next pre-baked region grab each call, repeating the last."""

    include_cursor = False

    def __init__(self, results):
        self._results = results
        self._i = 0

    def capture_region(self, region):
        result = self._results[min(self._i, len(self._results) - 1)]
        self._i += 1
        return result


class _RecordingScroller:
    def __init__(self):
        self.calls = []

    def scroll(self, clicks, *, at=None):
        self.calls.append((clicks, at))


def test_scrolling_auto_drives_the_scroller():
    results = [_result(10, range(off, off + 30)) for off in (0, 10, 20)]
    capturer = _SequenceCapturer(results)
    scroller = _RecordingScroller()
    image, _target, count = headless.perform_scrolling_capture(
        capturer,
        REGION,
        allowlist=None,
        settle=2,
        scroller=scroller,
        scroll_clicks=3,
        sleep=lambda *_: None,
    )
    assert count == 3
    assert image.height() == 50  # 30 + 10 + 10
    # The loop turned the wheel down (negative) at the region centre (5, 15).
    assert scroller.calls
    assert all(clicks == -3 and at == (5, 15) for clicks, at in scroller.calls)


class _WindowedCapturer(_SequenceCapturer):
    """A sequence capturer that also reports on-screen windows (for the blocklist)."""

    def __init__(self, results, windows):
        super().__init__(results)
        self._windows = windows

    def list_windows(self):
        return self._windows


def test_scrolling_redacts_blocklisted_window():
    from shotquill import blocklist as bl
    from shotquill.capture.base import WindowInfo

    region = Rect(0, 0, 10, 30)
    results = [_result(10, range(off, off + 30)) for off in (0, 10, 20)]
    # A blocklisted window covers the whole region, so every sampled frame is
    # redacted to black before stitching.
    secret = WindowInfo(1, "Secret", "", Rect(0, 0, 10, 30), bundle_id="com.secret")
    blocklist = bl.Blocklist(rules=(bl.BlockRule(bundle_id="com.secret"),))
    capturer = _WindowedCapturer(results, [secret])
    image, _target, _count = headless.perform_scrolling_capture(
        capturer,
        region,
        blocklist=blocklist,
        allowlist=None,
        settle=2,
        scroller=_RecordingScroller(),
        sleep=lambda *_: None,
    )
    px = image.pixelColor(0, 0)
    assert (px.red(), px.green(), px.blue()) == (0, 0, 0)  # painted out


def test_get_scroller_refused_on_wayland(monkeypatch):
    from shotquill import scroll

    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    with pytest.raises(headless.CapabilityUnsupported) as exc:
        scroll.get_scroller()
    assert exc.value.exit_code == headless.EXIT_UNSUPPORTED


# --- CLI --scrolling validation (short-circuits before any capture) ----------


def test_cli_scrolling_requires_region():
    assert cli.main(["capture", "--scrolling"]) == 2


def test_cli_scrolling_conflicts_with_target_options():
    assert cli.main(["capture", "--scrolling", "--window-id", "5"]) == 2
    assert cli.main(["capture", "--scrolling", "--display", "0"]) == 2


def test_cli_scrolling_conflicts_with_interactive():
    assert cli.main(["capture", "--scrolling", "--interactive"]) == 2


def test_cli_scrolling_rejects_unsupported_postprocessing():
    assert cli.main(["capture", "--scrolling", "--region", "0,0,10,10", "--mask", "0,0,1,1"]) == 2
    assert cli.main(["capture", "--scrolling", "--region", "0,0,10,10", "--redact-pii"]) == 2


def test_cli_scrolling_rejects_non_positive_bounds():
    base = ["capture", "--scrolling", "--region", "0,0,10,10"]
    assert cli.main([*base, "--max-height", "0"]) == 2
    assert cli.main([*base, "--scroll-interval", "0"]) == 2
    assert cli.main([*base, "--scroll-clicks", "0"]) == 2


def test_cli_auto_requires_scrolling():
    assert cli.main(["capture", "--auto"]) == 2
