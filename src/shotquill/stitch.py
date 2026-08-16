# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Stitch a run of scrolling frames into one long image.

A long screenshot is many region grabs taken while the content scrolls, with each
frame overlapping the one before. This module is the platform-independent core: it
estimates how far the content moved between consecutive frames and composites the
new strip onto a growing canvas — no capture, no input synthesis, no Qt event
loop, so it is fully unit-testable headlessly.

Two wrinkles a naïve "paste each frame below the last" gets wrong, both handled
here:

* **Overlap.** Frames share a band of rows (the scroll step is smaller than the
  frame, on purpose, so there is something to align on). Pasting whole frames
  would duplicate that band; we measure the per-pair vertical offset and append
  only the genuinely new rows.
* **Sticky chrome.** Fixed headers, footers, and side rails repeat at the same
  screen position while the document moves. Left in, they would march down the
  long image and can also pull alignment toward a false offset. We keep
  top/bottom bands once and use independently moving vertical tiles to align
  around side rails; persistent side content is replaced by its background below
  the initial viewport.

The matcher keeps an exact fast path, ranks rare exact rows so flat backgrounds
and repeated line spacing cannot dominate the decision, then falls back to
sampled pixel similarity. That tolerates small fixed-position artifacts such as
a pointer or scrollbar while rejecting pairs without reliable overlap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtGui import QImage

# How many leading rows of the newer frame must line up with the older one for an
# overlap candidate to be considered. Small and cheap as a first filter; the real
# offset is then confirmed against the *whole* overlap, so a coincidental k-row
# match inside a blank or repeating band can't fix the offset on its own.
DEFAULT_MIN_OVERLAP = 8
MATCH_PIXEL_DELTA = 24
MATCH_MIN_SCORE = 0.78
SAME_FRAME_MIN_SCORE = 0.96
MATCH_ROW_SAMPLES = 36
MATCH_COLUMN_SAMPLES = 64
MATCH_COARSE_ROW_SAMPLES = 10
MATCH_COARSE_COLUMN_SAMPLES = 16
MATCH_CANDIDATE_LIMIT = 16
MATCH_DISTINCTIVE_ROW_MAX_FREQUENCY = 16
MATCH_MIN_DISTINCTIVE_SUPPORT = 4.0
MATCH_MIN_DISTINCTIVE_OVERLAP_FRACTION = 0.02
MATCH_REPETITIVE_ROW_FRACTION = 0.50
MATCH_OFFSET_PENALTY = 0.10
MATCH_MIN_RELIABLE_OVERLAP = 64
MATCH_TILE_TARGET_WIDTH = 48
MATCH_TILE_MAX_COUNT = 64
MATCH_TILE_MIN_SCROLL_FRACTION = 0.30
MATCH_TILE_MIN_SCROLL_COUNT = 2
MATCH_TILE_MIN_DISTINCTIVE_SUPPORT = 0.25
MATCH_TILE_MIN_DISTINCTIVE_DENSITY = 0.004
MATCH_TILE_MIN_TOTAL_SUPPORT = 4.0
FIXED_SIDE_BLOCK_WIDTH = 8
FIXED_SIDE_ROW_SAMPLES = 96
FIXED_SIDE_MIN_MATCH = 0.80
FIXED_SIDE_MIN_PREFERENCE = 0.02
FIXED_SIDE_MIN_FRACTION = 0.03
FIXED_SIDE_MIN_PIXELS = 24


class StitchError(ValueError):
    """Raised when a frame sequence cannot be stitched without losing content."""


class ScrollAlignmentError(StitchError):
    """Raised when the latest sample cannot yet align with the last kept frame."""


class NoScrollingDetected(StitchError):
    """Raised when an automatic/manual long capture never observes any motion."""


def _rows(image: QImage) -> tuple[list[bytes], int, int]:
    """Split an image into a list of per-row RGBA byte strings, plus (w, h).

    Rows are compared by exact bytes (not a hash) so equality is collision-free.
    Uses the image's own stride and trims each row to ``width * 4`` so any
    end-of-line padding never leaks into the comparison.
    """
    from PySide6.QtGui import QImage

    img = image.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    stride = img.bytesPerLine()
    row_bytes = w * 4
    buf = bytes(img.constBits())[: stride * h]
    return [buf[y * stride : y * stride + row_bytes] for y in range(h)], w, h


def _sticky_from_rows(prev: list[bytes], curr: list[bytes], h: int) -> tuple[int, int]:
    """Leading/trailing run of rows identical at the same index between two frames."""
    head = 0
    while head < h and prev[head] == curr[head]:
        head += 1
    foot = 0
    while foot < h - head and prev[h - 1 - foot] == curr[h - 1 - foot]:
        foot += 1
    return head, foot


def _sample_positions(size: int, limit: int) -> tuple[int, ...]:
    """Return evenly distributed indices without allocating a full-size range."""
    if size <= 0:
        return ()
    if size <= limit:
        return tuple(range(size))
    if limit <= 1:
        return (size // 2,)
    return tuple(sorted({round(i * (size - 1) / (limit - 1)) for i in range(limit)}))


def _row_similarity(
    left: bytes, right: bytes, *, column_samples: int = MATCH_COLUMN_SAMPLES
) -> float:
    """Approximate RGB similarity for one row using evenly sampled pixels."""
    width = min(len(left), len(right)) // 4
    xs = _sample_positions(width, column_samples)
    if not xs:
        return 0.0
    score = 0.0
    for x in xs:
        offset = x * 4
        mean_delta = (
            sum(abs(left[offset + channel] - right[offset + channel]) for channel in range(3)) / 3
        )
        score += max(0.0, 1.0 - mean_delta / MATCH_PIXEL_DELTA)
    return score / len(xs)


def _overlap_similarity(
    prev: list[bytes],
    curr: list[bytes],
    *,
    head: int,
    foot: int,
    dy: int,
    row_samples: int = MATCH_ROW_SAMPLES,
    column_samples: int = MATCH_COLUMN_SAMPLES,
) -> float:
    """Similarity of the overlap at ``dy``, trimming localized screen artifacts."""
    end = len(prev) - foot - dy
    overlap = end - head
    if overlap <= 0:
        return 0.0
    positions = _sample_positions(overlap, row_samples)
    scores = sorted(
        _row_similarity(
            prev[head + dy + row],
            curr[head + row],
            column_samples=column_samples,
        )
        for row in positions
    )
    # A pointer, scrollbar thumb, or small animation can corrupt a few complete
    # rows. Ignore the worst fifth while retaining enough samples to reject a
    # genuinely unrelated pair.
    trim = len(scores) // 5
    kept = scores[trim:] if trim else scores
    return sum(kept) / len(kept)


def _match_quality(score: float, dy: int, band: int) -> float:
    """Rank equally plausible matches toward the one with more overlap."""
    return score - MATCH_OFFSET_PENALTY * dy / max(1, band)


def _required_overlap(band: int, min_overlap: int) -> int:
    """Require more evidence on a large viewport than the tiny anchor alone."""
    scaled = min(MATCH_MIN_RELIABLE_OVERLAP, max(1, band // 4))
    return min(band, max(1, min_overlap, scaled))


def _distinctive_offset_candidates(
    prev: list[bytes],
    curr: list[bytes],
    *,
    head: int,
    foot: int,
    max_dy: int,
) -> list[tuple[float, int, int]]:
    """Rank offsets by exact matches of rare, content-bearing rows.

    Uniform backgrounds can occupy hundreds of identical rows and must not count
    as alignment evidence. Rare rows preserve text and image detail; accumulating
    their absolute support also keeps a broad noisy overlap ahead of a tiny exact
    match in repeated content.
    """
    end = len(prev) - foot
    prev_positions: dict[bytes, list[int]] = {}
    curr_positions: dict[bytes, list[int]] = {}
    for index, row in enumerate(prev[head:end]):
        prev_positions.setdefault(row, []).append(index)
    for index, row in enumerate(curr[head:end]):
        curr_positions.setdefault(row, []).append(index)

    support: dict[int, float] = {}
    for row, prev_at in prev_positions.items():
        curr_at = curr_positions.get(row)
        if curr_at is None:
            continue
        frequency = max(len(prev_at), len(curr_at))
        if frequency > MATCH_DISTINCTIVE_ROW_MAX_FREQUENCY:
            continue
        weight = 1.0 / frequency
        for prev_index in prev_at:
            for curr_index in curr_at:
                dy = prev_index - curr_index
                if 0 <= dy <= max_dy:
                    support[dy] = support.get(dy, 0.0) + weight

    return sorted(
        ((value, -dy, dy) for dy, value in support.items()),
        reverse=True,
    )


def _repetitive_row_fraction(rows: list[bytes]) -> float:
    """Fraction of rows belonging to a heavily repeated flat pattern."""
    if not rows:
        return 1.0
    frequencies: dict[bytes, int] = {}
    for row in rows:
        frequencies[row] = frequencies.get(row, 0) + 1
    repetitive = sum(
        count for count in frequencies.values() if count > MATCH_DISTINCTIVE_ROW_MAX_FREQUENCY
    )
    return repetitive / len(rows)


def _repetitive_rows_dominate(
    prev: list[bytes], curr: list[bytes], *, head: int, foot: int
) -> bool:
    """Whether sampled-pixel similarity would mostly measure flat background."""
    end = len(prev) - foot
    return (
        min(
            _repetitive_row_fraction(prev[head:end]),
            _repetitive_row_fraction(curr[head:end]),
        )
        >= MATCH_REPETITIVE_ROW_FRACTION
    )


def _same_position_similar(
    prev: list[bytes], curr: list[bytes], h: int, head: int = 0, foot: int = 0
) -> bool:
    """Whether two frames are effectively unchanged at their screen positions."""
    if prev == curr:
        return True
    distinctive = _distinctive_offset_candidates(prev, curr, head=head, foot=foot, max_dy=0)
    if distinctive and distinctive[0][0] >= MATCH_MIN_DISTINCTIVE_SUPPORT:
        support = distinctive[0][0]
        band = max(1, h - head - foot)
        if support / band < MATCH_MIN_DISTINCTIVE_OVERLAP_FRACTION:
            return False
        return _overlap_similarity(prev, curr, head=head, foot=foot, dy=0) >= SAME_FRAME_MIN_SCORE
    if _repetitive_rows_dominate(prev, curr, head=head, foot=foot):
        return False
    return _overlap_similarity(prev, curr, head=head, foot=foot, dy=0) >= SAME_FRAME_MIN_SCORE


def _offset_from_rows_single(
    prev: list[bytes], curr: list[bytes], h: int, head: int, foot: int, min_overlap: int
) -> int | None:
    """Vertical scroll offset between two frames within the scrolling band.

    Returns the best-supported ``dy`` such that ``curr``'s band, shifted up by
    ``dy``, matches ``prev``'s band over their overlap — i.e. the content scrolled
    up by ``dy``. Exact distinctive rows carry more weight than uniform or repeated
    backgrounds. ``0`` means the band is unchanged (no scroll / reached the
    bottom); ``None`` means no reliable overlap was found (the frames are disjoint,
    e.g. a scroll larger than the visible band).
    """
    band = h - head - foot
    if band <= 0:
        return 0
    k = min(max(1, min_overlap), band)
    required_overlap = _required_overlap(band, min_overlap)
    max_dy = band - required_overlap
    anchor = curr[head : head + k]
    # Most wheel steps retain at least three quarters of the viewport. Preserve
    # the byte-exact fast path there; a far-away exact match can be a short,
    # repeated pattern and must compete with tolerant higher-overlap candidates.
    fast_max_dy = min(max_dy, band // 4)
    for dy in range(0, fast_max_dy + 1):
        if prev[head + dy : head + dy + k] != anchor:
            continue
        # The k-row anchor matched; confirm the *whole* remaining overlap agrees
        # before trusting it. A blank or repeating band can match the anchor at a
        # dy smaller than the real scroll, and appending only those rows would drop
        # the genuinely-new content below — so the true offset is the smallest dy
        # whose entire overlap lines up, not merely its first k rows.
        if prev[head + dy : h - foot] == curr[head : h - foot - dy]:
            return dy

    # Sparse pages can look almost identical at every offset because their
    # background dominates sampled pixels. Rare exact rows carry the real text
    # and image structure. Rank by their absolute support so a broad overlap
    # with a small fixed artifact still beats a tiny repeated match.
    distinctive_candidates = _distinctive_offset_candidates(
        prev,
        curr,
        head=head,
        foot=foot,
        max_dy=max_dy,
    )
    saw_distinctive_evidence = False
    for support, _prefer_smaller, dy in distinctive_candidates[:MATCH_CANDIDATE_LIMIT]:
        if support < MATCH_MIN_DISTINCTIVE_SUPPORT:
            break
        saw_distinctive_evidence = True
        overlap = band - dy
        if support / overlap < MATCH_MIN_DISTINCTIVE_OVERLAP_FRACTION:
            continue
        if _overlap_similarity(prev, curr, head=head, foot=foot, dy=dy) >= MATCH_MIN_SCORE:
            return dy

    # Weak periodic matches are evidence of ambiguity, not a valid scroll. Once
    # exact rows disagree on the offset, sampled background similarity cannot
    # safely resolve it.
    if saw_distinctive_evidence:
        return None

    # With no rare-row support, a flat/repetitive page has no reliable evidence
    # for the tolerant matcher; rejecting it is safer than silently dropping content.
    if _repetitive_rows_dominate(prev, curr, head=head, foot=foot):
        return None

    # Coarsely rank every plausible offset, then spend the more expensive sample
    # budget only on the strongest candidates. The overlap penalty prevents a
    # tiny repeated strip near the bottom from outranking a nearly-perfect match
    # backed by most of the viewport.
    candidates: list[tuple[float, int, int]] = []
    for dy in range(0, max_dy + 1):
        score = _overlap_similarity(
            prev,
            curr,
            head=head,
            foot=foot,
            dy=dy,
            row_samples=MATCH_COARSE_ROW_SAMPLES,
            column_samples=MATCH_COARSE_COLUMN_SAMPLES,
        )
        candidates.append((_match_quality(score, dy, band), -dy, dy))
    candidates.sort(reverse=True)

    best_dy: int | None = None
    best_quality = float("-inf")
    for _coarse_quality, _prefer_smaller, dy in candidates[:MATCH_CANDIDATE_LIMIT]:
        score = _overlap_similarity(prev, curr, head=head, foot=foot, dy=dy)
        quality = _match_quality(score, dy, band)
        if score >= MATCH_MIN_SCORE and quality > best_quality:
            best_dy = dy
            best_quality = quality
    return best_dy


def _tile_bounds(width: int) -> list[tuple[int, int]]:
    """Split the image into narrow vertical tiles without producing empty slices."""
    if width <= 0:
        return []
    count = min(
        MATCH_TILE_MAX_COUNT,
        max(1, (width + MATCH_TILE_TARGET_WIDTH - 1) // MATCH_TILE_TARGET_WIDTH),
    )
    edges = [round(index * width / count) for index in range(count + 1)]
    return [
        (left, right) for left, right in zip(edges[:-1], edges[1:], strict=True) if right > left
    ]


def _tile_offset_evidence(
    prev: list[bytes],
    curr: list[bytes],
    *,
    head: int,
    foot: int,
    min_overlap: int,
) -> list[tuple[int, int, int | None, float]]:
    """Return each vertical tile's independently supported scroll offset.

    Full rows are a mixture of motions on pages with a fixed navigation rail:
    the rail votes for zero while the document votes for the real scroll. Rare
    exact row fragments let the wider moving region win without trusting flat
    background pixels.
    """
    if not prev:
        return []
    width = len(prev[0]) // 4
    band = len(prev) - head - foot
    required_overlap = _required_overlap(band, min_overlap)
    max_dy = band - required_overlap
    if max_dy < 0:
        return []

    evidence: list[tuple[int, int, int | None, float]] = []
    for left, right in _tile_bounds(width):
        start = left * 4
        end = right * 4
        prev_tile = [row[start:end] for row in prev]
        curr_tile = [row[start:end] for row in curr]
        winner: int | None = None
        winner_support = 0.0
        ranked: list[tuple[float, float, int, int]] = []
        candidates = _distinctive_offset_candidates(
            prev_tile,
            curr_tile,
            head=head,
            foot=foot,
            max_dy=max_dy,
        )
        for support, _prefer_smaller, dy in candidates[:MATCH_CANDIDATE_LIMIT]:
            if support < MATCH_TILE_MIN_DISTINCTIVE_SUPPORT:
                break
            overlap = band - dy
            density = support / overlap
            if density < MATCH_TILE_MIN_DISTINCTIVE_DENSITY:
                continue
            if (
                _overlap_similarity(prev_tile, curr_tile, head=head, foot=foot, dy=dy)
                < MATCH_MIN_SCORE
            ):
                continue
            ranked.append((density, support, -dy, dy))
        if ranked:
            _density, winner_support, _prefer_smaller, winner = max(ranked)
        evidence.append((left, right, winner, winner_support))
    return evidence


def _tile_scroll_consensus(
    evidence: list[tuple[int, int, int | None, float]], width: int
) -> int | None:
    """Choose a positive offset backed by multiple independently moving tiles."""
    votes: dict[int, tuple[int, int, float]] = {}
    for left, right, dy, support in evidence:
        if dy is None or dy <= 0:
            continue
        vote_width, count, total_support = votes.get(dy, (0, 0, 0.0))
        votes[dy] = (vote_width + right - left, count + 1, total_support + support)
    if not votes:
        return None
    dy, (vote_width, count, support) = max(
        votes.items(),
        key=lambda item: (item[1][0], item[1][1], item[1][2], -item[0]),
    )
    minimum_width = max(
        MATCH_TILE_TARGET_WIDTH * MATCH_TILE_MIN_SCROLL_COUNT,
        round(width * MATCH_TILE_MIN_SCROLL_FRACTION),
    )
    if (
        count < MATCH_TILE_MIN_SCROLL_COUNT
        or vote_width < minimum_width
        or support < MATCH_TILE_MIN_TOTAL_SUPPORT
    ):
        return None
    return dy


def _side_motion_blocks(
    prev: list[bytes],
    curr: list[bytes],
    *,
    head: int,
    foot: int,
    dy: int,
) -> list[tuple[int, int, str]]:
    """Classify narrow column blocks as fixed, scrolling, or visually ambiguous."""
    if not prev or dy <= 0:
        return []
    width = len(prev[0]) // 4
    band = len(prev) - head - foot
    overlap = band - dy
    positions = _sample_positions(overlap, FIXED_SIDE_ROW_SAMPLES)
    if not positions:
        return []

    blocks: list[tuple[int, int, str]] = []
    for left in range(0, width, FIXED_SIDE_BLOCK_WIDTH):
        right = min(width, left + FIXED_SIDE_BLOCK_WIDTH)
        same = shifted = total = 0
        patterns: set[bytes] = set()
        for row in positions:
            same_row = prev[head + row]
            shifted_row = prev[head + dy + row]
            curr_row = curr[head + row]
            patterns.add(curr_row[left * 4 : right * 4])
            for x in range(left, right):
                start = x * 4
                end = start + 4
                same += same_row[start:end] == curr_row[start:end]
                shifted += shifted_row[start:end] == curr_row[start:end]
                total += 1
        same_score = same / total
        shifted_score = shifted / total
        if same_score >= 0.995 and len(patterns) >= 3:
            # A genuinely fixed control can be vertically periodic, making the
            # shifted comparison tie at 1.0. Exact same-position identity plus
            # real visual variation distinguishes it from a blank page margin.
            motion = "fixed"
        elif (
            same_score >= FIXED_SIDE_MIN_MATCH
            and same_score >= shifted_score + FIXED_SIDE_MIN_PREFERENCE
        ):
            motion = "fixed"
        elif (
            shifted_score >= FIXED_SIDE_MIN_MATCH
            and shifted_score >= same_score + FIXED_SIDE_MIN_PREFERENCE
        ):
            motion = "scrolling"
        else:
            motion = "ambiguous"
        blocks.append((left, right, motion))
    return blocks


def _fixed_side_bands(
    prev: list[bytes],
    curr: list[bytes],
    *,
    head: int,
    foot: int,
    dy: int,
) -> tuple[int, int]:
    """Width of persistent leading/trailing side chrome around moving content."""
    blocks = _side_motion_blocks(prev, curr, head=head, foot=foot, dy=dy)
    if not blocks:
        return (0, 0)
    width = blocks[-1][1]
    minimum = max(FIXED_SIDE_MIN_PIXELS, round(width * FIXED_SIDE_MIN_FRACTION))
    scrolling = [
        index for index, (_left, _right, motion) in enumerate(blocks) if motion == "scrolling"
    ]
    if not scrolling:
        return (0, 0)

    first_scrolling = scrolling[0]
    last_scrolling = scrolling[-1]
    left_edges = [
        right
        for index, (_left, right, motion) in enumerate(blocks)
        if index < first_scrolling and motion == "fixed"
    ]
    right_edges = [
        left
        for index, (left, _right, motion) in enumerate(blocks)
        if index > last_scrolling and motion == "fixed"
    ]
    left_band = max(left_edges, default=0)
    right_start = min(right_edges, default=width)
    right_band = width - right_start
    if left_band < minimum:
        left_band = 0
    if right_band < minimum:
        right_band = 0
    return left_band, right_band


def _alignment_from_rows(
    prev: list[bytes],
    curr: list[bytes],
    h: int,
    head: int,
    foot: int,
    min_overlap: int,
) -> tuple[int | None, int, int]:
    """Resolve mixed fixed/scrolling motion and describe persistent side bands."""
    width = len(prev[0]) // 4 if prev else 0
    evidence = _tile_offset_evidence(
        prev,
        curr,
        head=head,
        foot=foot,
        min_overlap=min_overlap,
    )
    tiled_dy = _tile_scroll_consensus(evidence, width)
    full_dy = _offset_from_rows_single(prev, curr, h, head, foot, min_overlap)
    tiled_sides = (0, 0)
    if tiled_dy is not None:
        tiled_sides = _fixed_side_bands(
            prev,
            curr,
            head=head,
            foot=foot,
            dy=tiled_dy,
        )
        # The tiled override exists for mixed motion around fixed side chrome.
        # Without such a region, sparse periodic fragments must neither override
        # the full-frame result nor turn a disjoint pair into a plausible partial.
        if tiled_sides == (0, 0) and tiled_dy != full_dy:
            tiled_dy = None
    dy = tiled_dy if tiled_dy is not None else full_dy
    if dy is None or dy <= 0:
        return dy, 0, 0
    if tiled_dy == dy:
        left, right = tiled_sides
    else:
        left, right = _fixed_side_bands(prev, curr, head=head, foot=foot, dy=dy)
    return dy, left, right


def _offset_from_rows(
    prev: list[bytes], curr: list[bytes], h: int, head: int, foot: int, min_overlap: int
) -> int | None:
    return _alignment_from_rows(prev, curr, h, head, foot, min_overlap)[0]


def _dominant_band_rgba(rows: list[bytes], left: int, right: int) -> tuple[int, int, int, int]:
    """Sample the most common color in a fixed side band for its extension fill."""
    if not rows or right <= left:
        return (0, 0, 0, 255)
    counts: dict[bytes, int] = {}
    for y in _sample_positions(len(rows), 64):
        row = rows[y]
        for relative_x in _sample_positions(right - left, 32):
            start = (left + relative_x) * 4
            pixel = row[start : start + 4]
            counts[pixel] = counts.get(pixel, 0) + 1
    pixel = max(counts, key=counts.get)
    return pixel[0], pixel[1], pixel[2], pixel[3]


def _fill_fixed_sides(
    image: QImage,
    left: int,
    right: int,
    left_rgba: tuple[int, int, int, int],
    right_rgba: tuple[int, int, int, int],
) -> None:
    """Remove repeated side chrome from an appended strip in place."""
    if left <= 0 and right <= 0:
        return
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColor, QPainter

    painter = QPainter(image)
    try:
        if left > 0:
            painter.fillRect(QRect(0, 0, left, image.height()), QColor(*left_rgba))
        if right > 0:
            painter.fillRect(
                QRect(image.width() - right, 0, right, image.height()),
                QColor(*right_rgba),
            )
    finally:
        painter.end()


def detect_sticky_bands(prev: QImage, curr: QImage) -> tuple[int, int]:
    """Height of the unchanging top and bottom bands shared by two frames.

    Returns ``(header, footer)`` in pixels. A sticky header/footer is rows that
    are byte-identical at the same position in both frames; everything between is
    the part that scrolls. Returns ``(0, 0)`` when the frames differ in size.
    """
    pr, w1, h1 = _rows(prev)
    cr, w2, h2 = _rows(curr)
    if (w1, h1) != (w2, h2):
        return (0, 0)
    return _sticky_from_rows(pr, cr, h1)


def estimate_vertical_offset(
    prev: QImage,
    curr: QImage,
    *,
    head: int = 0,
    foot: int = 0,
    min_overlap: int = DEFAULT_MIN_OVERLAP,
) -> int | None:
    """How far the content scrolled between two consecutive frames, in pixels.

    ``head`` / ``foot`` exclude a known sticky header / footer from the match (see
    :func:`detect_sticky_bands`); leave them ``0`` for content with no fixed bars.
    Returns ``0`` for an unchanged band (no scroll), a positive offset for a
    scroll, or ``None`` when no overlap can be found. Frames of differing size
    yield ``None``.
    """
    pr, w1, h1 = _rows(prev)
    cr, w2, h2 = _rows(curr)
    if (w1, h1) != (w2, h2):
        return None
    return _offset_from_rows(pr, cr, h1, head, foot, min_overlap)


def stitch_vertical(frames: list[QImage], *, min_overlap: int = DEFAULT_MIN_OVERLAP) -> QImage:
    """Composite overlapping scrolling frames into one tall image.

    Frames must share a width (raises ``ValueError`` otherwise) and are assumed to
    be in scroll order. A sticky header/footer common to the run is kept once; the
    overlap between each pair is measured and only the new rows are appended. A
    pair with no reliable overlap raises :class:`StitchError`.

    Returns a copy of the sole frame when given one, and raises ``ValueError`` on
    an empty input.
    """
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage, QPainter

    imgs = [f for f in frames if f is not None and not f.isNull()]
    if not imgs:
        raise ValueError("stitch_vertical needs at least one frame")
    if len(imgs) == 1:
        return imgs[0].copy()

    rows_list: list[list[bytes]] = []
    width = h = None
    for f in imgs:
        rows, w, fh = _rows(f)
        if width is None:
            width, h = w, fh
        elif (w, fh) != (width, h):
            raise ValueError(f"frames differ in size: {w}x{fh} != {width}x{h}")
        rows_list.append(rows)

    # Pass 1: the sticky header/footer is what stays identical across *every* pair
    # that actually scrolled, so take the minimum run over those pairs — a
    # coincidental extra match in one pair can only inflate its own run, never the
    # minimum.
    heads: list[int] = []
    foots: list[int] = []
    for i in range(1, len(imgs)):
        head_i, foot_i = _sticky_from_rows(rows_list[i - 1], rows_list[i], h)
        if head_i < h:  # the pair differs somewhere → it scrolled / changed
            heads.append(head_i)
            foots.append(foot_i)
    if not heads:  # every frame identical to its neighbour — nothing scrolled
        return imgs[0].copy()
    head = min(heads)
    foot = min(foots)
    band = h - head - foot

    # Pass 2: per-pair offsets under the global sticky bands. A disjoint pair is
    # an explicit failure: appending it would hide an unknown content gap.
    offsets: list[int] = []
    side_bands: list[tuple[int, int]] = []
    for i in range(1, len(imgs)):
        dy, left_i, right_i = _alignment_from_rows(
            rows_list[i - 1],
            rows_list[i],
            h,
            head,
            foot,
            min_overlap,
        )
        if dy is None:
            raise StitchError(f"no reliable overlap between frames {i} and {i + 1}")
        offsets.append(dy)
        if dy > 0:
            side_bands.append((left_i, right_i))

    left_fixed = min((left for left, _right in side_bands), default=0)
    right_fixed = min((right for _left, right in side_bands), default=0)
    left_fill = _dominant_band_rgba(rows_list[0], 0, left_fixed)
    right_fill = _dominant_band_rgba(rows_list[0], width - right_fixed, width)

    total_h = h + sum(offsets)
    canvas = QImage(width, total_h, QImage.Format.Format_RGBA8888)
    canvas.fill(0)

    painter = QPainter(canvas)
    try:
        first = imgs[0]
        if head > 0:
            painter.drawImage(QRect(0, 0, width, head), first, QRect(0, 0, width, head))
        painter.drawImage(QRect(0, head, width, band), first, QRect(0, head, width, band))
        y = head + band
        for i in range(1, len(imgs)):
            dy = offsets[i - 1]
            if dy <= 0:
                continue
            src_y = h - foot - dy
            strip = imgs[i].copy(0, src_y, width, dy)
            _fill_fixed_sides(strip, left_fixed, right_fixed, left_fill, right_fill)
            painter.drawImage(QRect(0, y, width, dy), strip)
            y += dy
        if foot > 0:
            footer = imgs[-1].copy(0, h - foot, width, foot)
            _fill_fixed_sides(footer, left_fixed, right_fixed, left_fill, right_fill)
            painter.drawImage(QRect(0, y, width, foot), footer)
    finally:
        painter.end()
    return canvas


class ScrollAccumulator:
    """Stitch a live scrolling capture incrementally, frame by frame.

    The sample → measure → append → stop logic, shared by both drivers: the
    blocking CLI loop in :func:`shotquill.headless.perform_scrolling_capture` and
    the GUI's non-blocking ``QTimer`` (each tick feeds one frame). Feed frames with
    :meth:`add`; when it returns ``False`` the scroll has settled or hit a limit,
    and :meth:`result` returns the finished long image. A ``settle`` value of
    ``None`` keeps a manually driven GUI session alive through pauses until
    :meth:`finish` is called.

    Unlike a batch :func:`stitch_vertical`, this keeps only what it needs as it
    goes — the header, the appended new-row strips, the most recent frame (for the
    footer), and the previous frame's rows (for the next comparison) — so memory is
    bounded by the *output* size plus one frame, not by the frame count. Sticky
    top/bottom bands and side rails are detected from the first pair that actually
    moves and excluded from repeated output (so initial timer samples cannot
    freeze the whole viewport into a "sticky" band). After movement starts, a
    frame that did not move (offset ``0``) is counted toward
    ``settle`` and dropped. The capture stops after ``settle`` still frames, once
    the height would exceed ``max_height``, or at the ``max_frames`` safety cap.
    """

    def __init__(
        self,
        *,
        max_height: int,
        settle: int | None,
        max_frames: int,
        start_frames: int | None = 25,
        min_overlap: int = DEFAULT_MIN_OVERLAP,
    ) -> None:
        self._max_height = max_height
        self._settle = settle
        self._max_frames = max_frames
        self._start_frames = start_frames
        self._min_overlap = min_overlap
        self._first: QImage | None = None  # held until the first scroll seeds the body
        self._prev_rows: list[bytes] | None = None  # rows of the most recent kept frame
        self._last: QImage | None = None  # most recent kept frame — source of the footer
        self._header: QImage | None = None  # the sticky header, kept once
        self._body: list[QImage] = []  # new-row strips, in scroll order (already cropped)
        self._head = 0
        self._foot = 0
        self._left_fixed = 0
        self._right_fixed = 0
        self._left_fill = (0, 0, 0, 255)
        self._right_fill = (0, 0, 0, 255)
        self._sticky_set = False
        self._body_height = 0
        self._width = 0
        self._frame_h = 0
        self._count = 0
        self._samples = 0
        self._unchanged = 0
        self._done = False

    @property
    def frame_count(self) -> int:
        return self._count

    @property
    def done(self) -> bool:
        return self._done

    def add(self, image: QImage) -> bool:
        """Take one sampled frame; return ``True`` to keep sampling, ``False`` when finished."""
        if self._done:
            return False
        rows, w, h = _rows(image)
        self._samples += 1
        if self._prev_rows is None:
            self._first = image
            self._last = image
            self._prev_rows = rows
            self._width, self._frame_h = w, h
            self._count = 1
            self._done = h >= self._max_height or self._samples >= self._max_frames
            return not self._done
        if (w, h) != (self._width, self._frame_h):
            self._done = True
            raise StitchError("scrolling frame size changed during capture")
        if not self._sticky_set:
            head, foot = _sticky_from_rows(self._prev_rows, rows, h)
            dy, left_fixed, right_fixed = _alignment_from_rows(
                self._prev_rows,
                rows,
                h,
                head,
                foot,
                self._min_overlap,
            )
            if dy == 0 or (
                dy is None and _same_position_similar(self._prev_rows, rows, h, head, foot)
            ):
                if self._start_frames is None and self._samples >= self._max_frames:
                    self._done = True
                    return False
                if self._start_frames is not None and self._samples >= min(
                    self._start_frames, self._max_frames
                ):
                    self._done = True
                    raise NoScrollingDetected(
                        "no scrolling was detected; scroll the selected area before finishing"
                    )
                return True
            if dy is None:
                if self._samples >= self._max_frames:
                    self._done = True
                    return False
                raise ScrollAlignmentError("no reliable overlap before scrolling started")
            self._left_fixed = left_fixed
            self._right_fixed = right_fixed
            self._left_fill = _dominant_band_rgba(self._prev_rows, 0, left_fixed)
            self._right_fill = _dominant_band_rgba(
                self._prev_rows,
                w - right_fixed,
                w,
            )
            self._seed_body(head, foot)
        else:
            dy, _left_fixed, _right_fixed = _alignment_from_rows(
                self._prev_rows,
                rows,
                h,
                self._head,
                self._foot,
                self._min_overlap,
            )
        if dy is None:
            if self._samples >= self._max_frames:
                self._done = True
                return False
            raise ScrollAlignmentError("no reliable overlap between scrolling frames")
        if dy == 0:
            # The view did not move since the last sample. Count it toward the
            # settle threshold and drop the duplicate (don't advance ``prev``).
            self._unchanged += 1
            if (
                self._settle is not None and self._unchanged >= self._settle
            ) or self._samples >= self._max_frames:
                self._done = True
                return False
            return True
        self._unchanged = 0
        new_rows = dy
        src_y = h - self._foot - dy
        strip = image.copy(0, src_y, w, new_rows)
        _fill_fixed_sides(
            strip,
            self._left_fixed,
            self._right_fixed,
            self._left_fill,
            self._right_fill,
        )
        self._body.append(strip)
        self._body_height += new_rows
        self._last = image
        self._prev_rows = rows
        self._count += 1
        if (
            self._head + self._body_height + self._foot >= self._max_height
            or self._samples >= self._max_frames
        ):
            self._done = True
            return False
        return True

    def _seed_body(self, head: int, foot: int) -> None:
        """On the first scrolling pair, lock the sticky bands and seed the body with
        the first frame's band (header/footer are composited once at the end)."""
        self._head = head
        self._foot = foot
        first = self._first
        band = self._frame_h - self._head - self._foot
        if self._head > 0:
            self._header = first.copy(0, 0, self._width, self._head)
        self._body.append(first.copy(0, self._head, self._width, band))
        self._body_height = band
        self._sticky_set = True
        self._first = None  # the band/header are captured; drop the full first frame

    def finish(self) -> QImage:
        """Finish an explicitly controlled manual session and return its image."""
        self._done = True
        if not self._sticky_set:
            raise NoScrollingDetected(
                "no scrolling was detected; scroll the selected area before finishing"
            )
        return self.result()

    def result(self) -> QImage:
        """Composite the finished long image, cropped to ``max_height``."""
        if not self._sticky_set:
            # Never scrolled past the first frame — it is the whole result.
            if self._first is None:
                raise ValueError("ScrollAccumulator has no frames")
            image = self._first.copy()
            if image.height() > self._max_height:
                image = image.copy(0, 0, image.width(), self._max_height)
            return image

        from PySide6.QtCore import QRect
        from PySide6.QtGui import QImage, QPainter

        total = self._head + self._body_height + self._foot
        canvas = QImage(self._width, total, QImage.Format.Format_RGBA8888)
        canvas.fill(0)
        painter = QPainter(canvas)
        try:
            y = 0
            if self._header is not None:
                painter.drawImage(QRect(0, 0, self._width, self._head), self._header)
                y = self._head
            for strip in self._body:
                painter.drawImage(QRect(0, y, self._width, strip.height()), strip)
                y += strip.height()
            if self._foot > 0 and self._last is not None:
                footer = self._last.copy(
                    0,
                    self._frame_h - self._foot,
                    self._width,
                    self._foot,
                )
                _fill_fixed_sides(
                    footer,
                    self._left_fixed,
                    self._right_fixed,
                    self._left_fill,
                    self._right_fill,
                )
                painter.drawImage(
                    QRect(0, y, self._width, self._foot),
                    footer,
                )
        finally:
            painter.end()
        if canvas.height() > self._max_height:
            canvas = canvas.copy(0, 0, self._width, self._max_height)
        return canvas
