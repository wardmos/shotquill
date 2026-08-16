# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for stitching scrolling frames into one long image."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import (  # noqa: E402
    QColor,
    QImage,
    QPainter,
)

from shotquill.stitch import (  # noqa: E402
    ScrollAccumulator,
    ScrollAlignmentError,
    StitchError,
    detect_sticky_bands,
    estimate_vertical_offset,
    stitch_vertical,
)


def _row_color(seed: int) -> QColor:
    # Distinct, deterministic per-row colour so every row is unique and the
    # overlap matcher has an unambiguous alignment to find.
    return QColor(seed % 251, (seed * 7) % 251, (seed * 13) % 251)


def _image(width: int, row_colors: list[QColor]) -> QImage:
    img = QImage(width, len(row_colors), QImage.Format.Format_RGBA8888)
    for y, color in enumerate(row_colors):
        for x in range(width):
            img.setPixelColor(x, y, color)
    return img


def _tall(width: int, n_rows: int, start: int = 0) -> list[QColor]:
    return [_row_color(start + y) for y in range(n_rows)]


def _crop(source: QImage, y: int, height: int) -> QImage:
    return source.copy(0, y, source.width(), height)


def _row_seed(img: QImage, y: int) -> tuple[int, int, int]:
    c = img.pixelColor(0, y)
    return (c.red(), c.green(), c.blue())


# --- estimate_vertical_offset -------------------------------------------------


def test_estimate_offset_finds_the_scroll_distance():
    source = _image(10, _tall(10, 40))
    prev = _crop(source, 0, 20)
    curr = _crop(source, 5, 20)  # scrolled down 5px (15-row overlap)
    assert estimate_vertical_offset(prev, curr) == 5


def test_estimate_offset_zero_for_identical_frames():
    frame = _image(10, _tall(10, 12))
    assert estimate_vertical_offset(frame, frame) == 0


def test_estimate_offset_none_when_disjoint():
    # Two crops far enough apart that they share no rows → no overlap to find.
    source = _image(10, _tall(10, 60))
    prev = _crop(source, 0, 12)
    curr = _crop(source, 40, 12)
    assert estimate_vertical_offset(prev, curr) is None


def test_estimate_offset_none_on_size_mismatch():
    a = _image(10, _tall(10, 12))
    b = _image(8, _tall(8, 12))
    assert estimate_vertical_offset(a, b) is None


def test_estimate_offset_respects_sticky_header():
    # A fixed 3-row header sits on top. The tolerant fallback can recover the
    # offset on its own; explicitly excluding the known header remains exact.
    header = [_row_color(900 + i) for i in range(3)]
    band = _tall(10, 40, start=100)
    prev = _image(10, header + band[0:20])
    curr = _image(10, header + band[5:25])
    assert estimate_vertical_offset(prev, curr) == 5
    assert estimate_vertical_offset(prev, curr, head=3) == 5


# --- detect_sticky_bands ------------------------------------------------------


def test_detect_sticky_header_and_footer():
    header = [_row_color(900 + i) for i in range(3)]
    footer = [_row_color(800 + i) for i in range(2)]
    band = _tall(10, 30, start=100)
    prev = _image(10, header + band[0:7] + footer)
    curr = _image(10, header + band[5:12] + footer)
    assert detect_sticky_bands(prev, curr) == (3, 2)


def test_detect_sticky_none_when_everything_scrolls():
    source = _image(10, _tall(10, 40))
    prev = _crop(source, 0, 12)
    curr = _crop(source, 4, 12)
    assert detect_sticky_bands(prev, curr) == (0, 0)


# --- stitch_vertical ----------------------------------------------------------


def test_stitch_single_frame_returns_a_copy():
    frame = _image(10, _tall(10, 12))
    out = stitch_vertical([frame])
    assert (out.width(), out.height()) == (10, 12)
    assert out is not frame


def test_stitch_empty_raises():
    with pytest.raises(ValueError, match="at least one"):
        stitch_vertical([])


def test_stitch_rejects_width_mismatch():
    with pytest.raises(ValueError, match="differ in size"):
        stitch_vertical([_image(10, _tall(10, 8)), _image(8, _tall(8, 8))])


def test_stitch_reconstructs_the_scrolled_content():
    # Frames are 12-row windows onto a 24-row page, stepping 4px each.
    source = _image(10, _tall(10, 24))
    frames = [_crop(source, off, 12) for off in (0, 4, 8, 12)]
    out = stitch_vertical(frames)
    # Height = first frame (12) + 3 steps × 4 = 24, the whole page.
    assert (out.width(), out.height()) == (10, 24)
    for y in range(24):
        assert _row_seed(out, y) == _row_seed(source, y)


def test_stitch_identical_frames_collapse_to_one():
    # No scroll happened (reached bottom immediately) → just the single frame.
    frame = _image(10, _tall(10, 12))
    out = stitch_vertical([frame, frame, frame])
    assert (out.width(), out.height()) == (10, 12)


def test_stitch_keeps_sticky_header_and_footer_once():
    header = [_row_color(900 + i) for i in range(3)]
    footer = [_row_color(800 + i) for i in range(2)]
    band_src = _tall(10, 40, start=100)  # the scrolling content
    # Two frames, band window of 20 rows, stepping 5px (15-row overlap).
    frames = [
        _image(10, header + band_src[0:20] + footer),
        _image(10, header + band_src[5:25] + footer),
    ]
    out = stitch_vertical(frames)
    # Height = frame (25) + one 5px step = 30.
    assert (out.width(), out.height()) == (10, 30)
    # Header once at the very top.
    for i in range(3):
        assert _row_seed(out, i) == (header[i].red(), header[i].green(), header[i].blue())
    # Footer once at the very bottom.
    for i in range(2):
        c = footer[i]
        assert _row_seed(out, 28 + i) == (c.red(), c.green(), c.blue())
    # Continuous band in between: rows 3..28 are band_src[0:25].
    for j in range(25):
        c = band_src[j]
        assert _row_seed(out, 3 + j) == (c.red(), c.green(), c.blue())


def test_stitch_disjoint_pair_is_an_explicit_failure():
    # A gap cannot be distinguished from skipped content. Silently appending the
    # whole frame would produce a plausible-looking but incomplete long screenshot.
    source = _image(10, _tall(10, 80))
    frames = [_crop(source, 0, 12), _crop(source, 60, 12)]
    with pytest.raises(StitchError, match="overlap"):
        stitch_vertical(frames)


# --- ScrollAccumulator (the live sample → decide → stitch driver) -------------


def _acc(max_height=10000, settle=3, max_frames=600):
    return ScrollAccumulator(max_height=max_height, settle=settle, max_frames=max_frames)


def test_accumulator_keeps_going_while_content_scrolls():
    page = _image(10, _tall(10, 100))
    acc = _acc()
    assert acc.add(_crop(page, 0, 30)) is True
    assert acc.add(_crop(page, 10, 30)) is True
    assert acc.frame_count == 2
    assert acc.done is False


def test_accumulator_stops_after_settle_and_drops_duplicates():
    page = _image(10, _tall(10, 100))
    a, b = _crop(page, 0, 30), _crop(page, 10, 30)
    acc = _acc(settle=2)
    assert acc.add(a) is True
    assert acc.add(b) is True
    assert acc.add(b) is True  # 1st still frame
    assert acc.add(b) is False  # 2nd still frame → settled
    assert acc.done is True
    assert acc.frame_count == 2  # the duplicates never joined the stitch
    assert acc.result().height() == 40


def test_accumulator_waits_for_motion_after_initial_still_frames():
    page = _image(10, _tall(10, 100))
    first, moved = _crop(page, 0, 30), _crop(page, 10, 30)
    acc = _acc(settle=2)

    assert acc.add(first) is True
    assert acc.add(first) is True  # user has not started scrolling yet
    assert acc.add(moved) is True  # later motion must still seed the stitch
    assert acc.add(moved) is True
    assert acc.add(moved) is False

    assert acc.frame_count == 2
    assert acc.result().height() == 40


def test_accumulator_reports_when_scrolling_never_starts():
    page = _image(10, _tall(10, 30))
    acc = ScrollAccumulator(max_height=1000, settle=3, max_frames=20, start_frames=3)

    assert acc.add(page) is True
    assert acc.add(page) is True
    with pytest.raises(StitchError, match="no scrolling was detected"):
        acc.add(page)

    assert acc.done is True
    assert acc.frame_count == 1


def test_accumulator_can_recover_after_a_missing_overlap():
    page = _image(10, _tall(10, 100))
    acc = _acc()
    assert acc.add(_crop(page, 0, 30)) is True
    with pytest.raises(ScrollAlignmentError, match="overlap"):
        acc.add(_crop(page, 60, 30))
    assert acc.done is False

    assert acc.add(_crop(page, 10, 30)) is True
    assert acc.frame_count == 2


def test_accumulator_stops_at_max_height():
    page = _image(10, _tall(10, 100))
    acc = _acc(max_height=35)
    assert acc.add(_crop(page, 0, 30)) is True  # approx height 30
    assert acc.add(_crop(page, 10, 30)) is False  # +10 → 40 ≥ 35, stop
    assert acc.result().height() == 35  # stitched 40, cropped back to the cap


def test_accumulator_caps_a_single_frame_to_max_height():
    page = _image(10, _tall(10, 30))
    acc = _acc(max_height=20)

    assert acc.add(page) is False
    assert acc.result().height() == 20


def test_accumulator_counts_still_samples_toward_max_frames():
    page = _image(10, _tall(10, 100))
    first, moved = _crop(page, 0, 30), _crop(page, 10, 30)
    acc = _acc(max_frames=3, settle=99)

    assert acc.add(first) is True
    assert acc.add(moved) is True
    assert acc.add(moved) is False
    assert acc.frame_count == 2


def test_accumulator_stops_at_max_frames():
    page = _image(10, _tall(10, 100))
    acc = _acc(max_frames=2)
    assert acc.add(_crop(page, 0, 30)) is True
    assert acc.add(_crop(page, 10, 30)) is False  # hit the 2-frame cap
    assert acc.frame_count == 2


def test_accumulator_reconstructs_scrolled_content():
    page = _image(10, _tall(10, 60))
    acc = _acc()
    for off in (0, 8, 16):
        acc.add(_crop(page, off, 30))
    out = acc.result()
    assert out.height() == 46  # 30 + 8 + 8
    for y in range(46):
        assert _row_seed(out, y) == _row_seed(page, y)


def test_accumulator_matches_batch_stitch_vertical():
    # The incremental accumulator and the batch stitch_vertical must agree pixel
    # for pixel on the same frame run (no drops / caps in play).
    page = _image(10, _tall(10, 80))
    frames = [_crop(page, off, 30) for off in (0, 8, 16, 24)]
    acc = _acc()
    for f in frames:
        acc.add(f)
    incremental = acc.result()
    batch = stitch_vertical(frames)
    assert (incremental.width(), incremental.height()) == (batch.width(), batch.height())
    for y in range(incremental.height()):
        assert _row_seed(incremental, y) == _row_seed(batch, y)


def test_accumulator_handles_sticky_header_instead_of_collapsing():
    # Regression: a fixed top bar >= min_overlap rows used to make every real
    # scroll read as "no motion" (the anchor was the header), so the capture
    # settled after one frame. The accumulator must detect the sticky band and
    # keep scrolling, with the header composited once.
    header = [_row_color(900 + i) for i in range(8)]
    band = _tall(10, 60, start=100)
    frames = [_image(10, header + band[off : off + 20]) for off in (0, 5, 10)]
    acc = _acc(settle=2)
    for f in frames:
        acc.add(f)
    acc.add(frames[-1])  # 1st still frame
    acc.add(frames[-1])  # 2nd → settle
    assert acc.frame_count == 3  # not collapsed to a single frame
    out = acc.result()
    assert out.height() == 38  # header 8 + band 20 + two 5px steps
    for i in range(8):  # header kept once at the top
        assert _row_seed(out, i) == (header[i].red(), header[i].green(), header[i].blue())
    for j in range(30):  # continuous band below it
        c = band[j]
        assert _row_seed(out, 8 + j) == (c.red(), c.green(), c.blue())


def test_estimate_offset_rejects_false_anchor_match_in_repeating_region():
    # Regression: the k-row anchor matches inside a repeating stripe at dy=0, but
    # the full overlap only agrees at the true scroll of 2. Verifying the whole
    # overlap (not just the anchor) picks the right offset.
    a, b = _row_color(1000), _row_color(1001)
    rows = [a if i % 2 == 0 else b for i in range(12)] + [_row_color(12 + i) for i in range(20)]
    page = _image(10, rows)
    prev = _crop(page, 0, 20)
    curr = _crop(page, 2, 20)
    assert estimate_vertical_offset(prev, curr) == 2


def test_estimate_offset_tolerates_a_fixed_screen_artifact():
    # A cursor, scrollbar thumb, or floating control stays at a screen position
    # while the page moves underneath it. That local mismatch must not turn the
    # entire pair into a disjoint frame.
    page = _image(30, _tall(30, 80))
    prev = _crop(page, 0, 30)
    curr = _crop(page, 10, 30)
    marker = QColor(255, 0, 0)
    for image in (prev, curr):
        for y in range(8, 14):
            for x in range(27, 30):
                image.setPixelColor(x, y, marker)

    assert estimate_vertical_offset(prev, curr) == 10


def test_estimate_offset_prefers_broad_overlap_over_a_short_repeated_match():
    # The row generator repeats after 251 rows. A fixed control breaks the true
    # exact match at 100px, while a 602px candidate happens to leave a tiny exact
    # repeated strip. Prefer the offset supported by most of the viewport.
    page = _image(30, _tall(30, 1400))
    prev = _crop(page, 0, 700)
    curr = _crop(page, 100, 700)
    marker = QColor(255, 0, 0)
    for image in (prev, curr):
        for y in range(200, 300):
            for x in range(27, 30):
                image.setPixelColor(x, y, marker)

    assert estimate_vertical_offset(prev, curr) == 100


def test_estimate_offset_uses_content_not_uniform_background():
    # A dark document has many matching background pixels and repeated line
    # spacing. The matcher must not mistake the line phase for the scroll.
    width, page_height, frame_height, scroll = 1600, 2400, 900, 650
    page = QImage(width, page_height, QImage.Format.Format_RGBA8888)
    page.fill(QColor(12, 17, 23))
    painter = QPainter(page)
    light = QColor(235, 240, 245)
    subtle = QColor(150, 160, 170)
    for line in range(40):
        y = 12 + line * 58
        for row in range(36):
            painter.fillRect(60, y + row, 120 + row * 7, 1, light)
            if row >= 8:
                x = 480 + ((line * 71 + row * 29) % 380)
                detail_width = 35 + ((line * 37 + row * 53) % 125)
                painter.fillRect(x, y + row, detail_width, 1, subtle)
    painter.end()

    prev = page.copy(0, 0, width, frame_height)
    curr = page.copy(0, scroll, width, frame_height)
    disjoint = page.copy(0, 1160, width, frame_height)

    assert estimate_vertical_offset(prev, curr) == scroll
    assert estimate_vertical_offset(prev, disjoint) is None

    gap_accumulator = _acc()
    assert gap_accumulator.add(prev) is True
    with pytest.raises(ScrollAlignmentError):
        gap_accumulator.add(disjoint)

    accumulator = _acc()
    assert accumulator.add(prev) is True
    assert accumulator.add(curr) is True
    assert accumulator.result() == page.copy(0, 0, width, frame_height + scroll)


def test_accumulator_keeps_the_correct_height_with_a_fixed_screen_artifact():
    page = _image(30, _tall(30, 80))
    frames = [_crop(page, offset, 30) for offset in (0, 10, 20)]
    marker = QColor(255, 0, 0)
    for image in frames:
        for y in range(8, 14):
            for x in range(27, 30):
                image.setPixelColor(x, y, marker)

    acc = _acc()
    for image in frames:
        assert acc.add(image) is True
    assert acc.result().height() == 50


def _fixed_sidebar_frames() -> tuple[list[QImage], QImage, QColor, int, int]:
    """GitHub-like frames: sparse scrolling content beside a fixed navigation rail."""
    sidebar_width = 96
    page_width = 288
    frame_height = 240
    scroll = 150
    background = QColor(13, 17, 23)

    page = QImage(page_width, frame_height + scroll * 2, QImage.Format.Format_RGBA8888)
    page.fill(background)
    painter = QPainter(page)
    for card in range(7):
        top = 18 + card * 82
        painter.fillRect(18, top, page_width - 36, 1, QColor(55, 62, 72))
        painter.fillRect(24, top + 14, 70 + card * 11, 7, QColor(235, 240, 245))
        painter.fillRect(24, top + 35, 150 + (card % 3) * 24, 4, QColor(145, 155, 166))
    painter.end()

    sidebar = QImage(sidebar_width, frame_height, QImage.Format.Format_RGBA8888)
    sidebar.fill(background)
    painter = QPainter(sidebar)
    for item in range(9):
        top = 10 + item * 25
        painter.fillRect(8, top, 12, 12, QColor(40, 190, 95))
        painter.fillRect(28, top + 3, 48 + (item % 2) * 16, 6, QColor(195, 202, 211))
    painter.end()

    frames: list[QImage] = []
    for offset in (0, scroll, scroll * 2):
        frame = QImage(
            sidebar_width + page_width,
            frame_height,
            QImage.Format.Format_RGBA8888,
        )
        frame.fill(background)
        painter = QPainter(frame)
        painter.drawImage(0, 0, sidebar)
        painter.drawImage(sidebar_width, 0, page.copy(0, offset, page_width, frame_height))
        painter.end()
        frames.append(frame)
    return frames, page, background, sidebar_width, scroll


def test_accumulator_aligns_scrolling_content_beside_a_fixed_sidebar():
    frames, page, background, sidebar_width, scroll = _fixed_sidebar_frames()

    assert estimate_vertical_offset(frames[0], frames[1]) == scroll

    acc = _acc()
    for frame in frames:
        assert acc.add(frame) is True
    out = acc.result()

    assert out.height() == frames[0].height() + scroll * 2
    assert out.copy(sidebar_width, 0, page.width(), page.height()) == page
    # The fixed navigation is shown once in the initial viewport. Its lower
    # fragments must not be pasted again with every newly appended strip.
    for y in range(frames[0].height(), out.height()):
        assert out.pixelColor(sidebar_width // 2, y) == background

    assert stitch_vertical(frames) == out
