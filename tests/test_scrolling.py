# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for the long-screenshot (scrolling) capture path.

The headless loop is driven through its ``source`` injection point (a list of
frames standing in for live region grabs) so the stitching, settle, and height-cap
logic is exercised deterministically with no display or timer.
The CLI tests cover the ``--scrolling`` flag's up-front validation, which short-
circuits before any capture.
"""

from __future__ import annotations

from types import SimpleNamespace

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
from shotquill.imaging import result_to_qimage  # noqa: E402

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
    image = result_to_qimage(image)
    assert count == 3
    assert (image.width(), image.height()) == (10, 50)  # 30 + 10 + 10
    for y in range(50):
        assert _row_seed(image, y) == _row_seed(page, y)
    assert target == "scrolling region 0,0,10,30"


def test_scrolling_preserves_scale_and_region_origin_in_capture_result():
    frames = []
    for offset in (0, 10):
        frame = _result(10, range(offset, offset + 30))
        frames.append(
            CaptureResult(
                width=frame.width,
                height=frame.height,
                scale=2.0,
                pixels=frame.pixels,
            )
        )

    result, _target, count = headless.perform_scrolling_capture(
        _DummyCapturer(), REGION, allowlist=None, source=frames
    )

    assert count == 2
    assert result.scale == 2.0
    assert (result.origin_x, result.origin_y) == (REGION.x, REGION.y)
    assert (result.width, result.height) == (10, 40)


def test_scrolling_stops_after_view_settles():
    page = _page(10, 100)
    a, b = _crop(page, 0, 30), _crop(page, 10, 30)
    # a → b is a real scroll; the trailing identical b's are the page sitting still.
    image, _target, count = headless.perform_scrolling_capture(
        _DummyCapturer(), REGION, allowlist=None, settle=2, source=[a, b, b, b]
    )
    image = result_to_qimage(image)
    assert count == 2  # the duplicate b's are dropped, not appended
    assert image.height() == 40  # 30 + one 10px step


def test_scrolling_single_frame_returns_that_frame():
    page = _page(10, 30)
    image, _target, count = headless.perform_scrolling_capture(
        _DummyCapturer(), REGION, allowlist=None, source=[_crop(page, 0, 30)]
    )
    image = result_to_qimage(image)
    assert count == 1
    assert image.height() == 30


def test_scrolling_caps_at_max_height():
    page = _page(10, 100)
    frames = [_crop(page, off, 30) for off in (0, 10, 20, 30, 40)]
    image, _target, _count = headless.perform_scrolling_capture(
        _DummyCapturer(), REGION, allowlist=None, max_height=45, source=frames
    )
    image = result_to_qimage(image)
    assert image.height() == 45  # grown past the cap, then cropped back to it


def test_scrolling_refused_when_allowlist_enabled():
    locked = al.Allowlist(enabled=True, rules=(bl.BlockRule(bundle_id="com.apple.Terminal"),))
    with pytest.raises(headless.CaptureBlocked) as exc:
        headless.perform_scrolling_capture(_DummyCapturer(), REGION, allowlist=locked, source=[])
    assert exc.value.exit_code == headless.EXIT_BLOCKED


def test_scrolling_refuses_a_backend_without_repeated_region_capture():
    class _SingleShotCapturer(_DummyCapturer):
        supports_repeated_region_capture = False

    with pytest.raises(headless.CapabilityUnsupported, match="single still"):
        headless.perform_scrolling_capture(
            _SingleShotCapturer(),
            REGION,
            blocklist=bl.Blocklist(),
            allowlist=al.Allowlist(),
        )


def test_scrolling_checks_blocklist_before_grabbing_each_frame():
    class _PolicyUnavailableCapturer:
        supports_repeated_region_capture = True
        include_cursor = False

        def __init__(self):
            self.captures = 0

        def list_windows(self):
            raise headless.CapabilityUnsupported("list_windows", "window enumeration disappeared")

        def capture_region(self, region):
            self.captures += 1
            return _result(10, range(30))

    capturer = _PolicyUnavailableCapturer()
    active = bl.Blocklist(
        rules=(
            bl.BlockRule(
                bundle_id="com.secret",
            ),
        )
    )
    with pytest.raises(headless.CaptureBlocked):
        headless.perform_scrolling_capture(
            capturer,
            REGION,
            blocklist=active,
            allowlist=al.Allowlist(),
            sleep=lambda *_: None,
        )
    assert capturer.captures == 0


# --- manual scrolling ---------------------------------------------------------


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


def test_manual_accumulator_waits_through_pauses_until_explicit_finish():
    from shotquill.stitch import ScrollAccumulator

    first = result_to_qimage(_result(10, range(30)))
    second = result_to_qimage(_result(10, range(10, 40)))
    accumulator = ScrollAccumulator(
        max_height=1000,
        settle=None,
        max_frames=20,
        start_frames=20,
    )

    assert accumulator.add(first) is True
    assert accumulator.add(second) is True
    for _ in range(5):
        assert accumulator.add(second) is True

    image = accumulator.finish()
    assert accumulator.done is True
    assert accumulator.frame_count == 2
    assert image.height() == 40


def test_manual_accumulator_refuses_finish_before_the_user_scrolls():
    from shotquill.stitch import NoScrollingDetected, ScrollAccumulator

    still = result_to_qimage(_result(10, range(30)))
    accumulator = ScrollAccumulator(
        max_height=1000,
        settle=None,
        max_frames=20,
        start_frames=20,
    )
    assert accumulator.add(still) is True

    with pytest.raises(NoScrollingDetected, match="no scrolling was detected"):
        accumulator.finish()


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
    # A blocklisted window covers the left half. Those pixels are black before
    # stitching, while the visible right half still supplies motion evidence.
    secret = WindowInfo(1, "Secret", "", Rect(0, 0, 5, 30), bundle_id="com.secret")
    blocklist = bl.Blocklist(rules=(bl.BlockRule(bundle_id="com.secret"),))
    capturer = _WindowedCapturer(results, [secret])
    image, _target, _count = headless.perform_scrolling_capture(
        capturer,
        region,
        blocklist=blocklist,
        allowlist=None,
        settle=2,
        sleep=lambda *_: None,
    )
    image = result_to_qimage(image)
    px = image.pixelColor(0, 0)
    assert (px.red(), px.green(), px.blue()) == (0, 0, 0)  # painted out
    assert image.height() == 50  # visible pixels still drove the three-frame stitch


# --- CLI --scrolling validation (short-circuits before any capture) ----------


def test_cli_scrolling_requires_region():
    assert cli.main(["capture", "--scrolling"]) == 2


def test_cli_scrolling_conflicts_with_target_options():
    assert cli.main(["capture", "--scrolling", "--window-id", "5"]) == 2
    assert cli.main(["capture", "--scrolling", "--display", "0"]) == 2


def test_cli_scrolling_conflicts_with_interactive():
    assert cli.main(["capture", "--scrolling", "--interactive"]) == 2


def test_cli_scrolling_runs_the_shared_postprocessing_pipeline(monkeypatch):
    from shotquill import imaging

    args = SimpleNamespace(
        scrolling=True,
        max_height=1000,
        scroll_interval=0.01,
        interactive=False,
        window_id=None,
        app=None,
        title=None,
        display=None,
        json=False,
    )
    captured = _result(10, range(30))
    monkeypatch.setattr(headless, "get_capturer", lambda **_kwargs: object())
    monkeypatch.setattr(
        headless,
        "perform_scrolling_capture",
        lambda *_args, **_kwargs: (captured, "scrolling region", 2),
    )
    events = []

    def _mask(result, masks):
        events.append(("mask", masks))
        return result

    def _redact(result, recognizer):
        events.append(("pii", recognizer))
        return result

    def _reveal(image, reveal, scale):
        events.append(("reveal", reveal, scale))
        return image

    monkeypatch.setattr(headless, "apply_masks", _mask)
    monkeypatch.setattr(headless, "redact_pii", _redact)
    monkeypatch.setattr(imaging, "pixelate_except", _reveal)

    recognizer = object()
    image, target, matched = cli._capture_image(
        args,
        REGION,
        include_cursor=False,
        masks=[Rect(0, 0, 1, 1)],
        reveal=[Rect(0, 0, 2, 2)],
        redact_pii_recognizer=recognizer,
    )

    assert target == "scrolling region"
    assert matched == 1
    assert [event[0] for event in events] == ["mask", "pii", "reveal"]
    assert (image.width(), image.height()) == (10, 30)


def test_cli_scrolling_accepts_postprocessing_and_session(monkeypatch, tmp_path):
    recognizer = object()
    seen = {}
    monkeypatch.setattr(headless, "get_recognizer", lambda: recognizer)

    def _capture(_args, region, **kwargs):
        seen["region"] = region
        seen.update(kwargs)
        image = QImage(10, 10, QImage.Format.Format_RGBA8888)
        image.fill(QColor("white"))
        return image, "scrolling region", 1

    def _record(session, _image, _target, *, dedup=False):
        seen["session"] = session
        seen["dedup"] = dedup
        return {"index": 1}

    monkeypatch.setattr(cli, "_capture_image", _capture)
    monkeypatch.setattr(cli, "_mirror_capture_observation", _record)
    monkeypatch.setattr(cli, "_save_image", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli.audit, "record", lambda *_args, **_kwargs: None)

    status = cli.main(
        [
            "capture",
            "--scrolling",
            "--region",
            "0,0,10,10",
            "--mask",
            "0,0,1,1",
            "--reveal",
            "0,0,2,2",
            "--redact-pii",
            "--session",
            "session-id",
            "--output",
            str(tmp_path / "long.png"),
        ]
    )

    assert status == 0
    assert seen["redact_pii_recognizer"] is recognizer
    assert len(seen["masks"]) == 1
    assert len(seen["reveal"]) == 1
    assert seen["session"] == "session-id"


def test_cli_scrolling_rejects_non_positive_bounds():
    base = ["capture", "--scrolling", "--region", "0,0,10,10"]
    assert cli.main([*base, "--max-height", "0"]) == 2
    assert cli.main([*base, "--scroll-interval", "0"]) == 2


@pytest.mark.parametrize("removed_args", (["--auto"], ["--scroll-clicks", "2"]))
def test_cli_rejects_removed_auto_scroll_options(removed_args):
    base = ["capture", "--scrolling", "--region", "0,0,10,10"]
    with pytest.raises(SystemExit) as exc_info:
        cli.main([*base, *removed_args])
    assert exc_info.value.code == 2
