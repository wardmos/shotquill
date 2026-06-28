#!/usr/bin/env python3
"""Render the ShotQuill app icon master PNG.

Produces a 1024x1024 PNG matching the brand mark (blue rounded tile + white
capture/pen glyph), following Apple's icon grid (~10% transparent margin around
the tile).

The committed ``icon.png`` is what ``build_dmg.sh`` converts into ``.icns`` with
macOS' native ``sips``/``iconutil``, so the build never depends on fonts being
present on the build machine. Re-run this only to regenerate the artwork::

    python3 packaging/macos/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 1024
SS = 4  # supersampling factor for crisp edges / anti-aliasing
MARGIN = 100  # Apple grid: tile sits inside a ~10% margin
RADIUS = 185  # rounded-tile corner radius (squircle-ish)

# Brand blue, as a top->bottom gradient around the menu-bar #2d7ff9.
TOP = (74, 155, 255)
BOTTOM = (31, 111, 224)

def _vertical_gradient(w: int, h: int) -> Image.Image:
    grad = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(h - 1, 1)
        grad.putpixel(
            (0, y),
            tuple(round(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3)),
        )
    return grad.resize((w, h))


def _rounded_mask(w: int, h: int, radius: int) -> Image.Image:
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    r = radius
    d.rectangle((r, 0, w - r, h), fill=255)
    d.rectangle((0, r, w, h - r), fill=255)
    for cx, cy in ((r, r), (w - r, r), (r, h - r), (w - r, h - r)):
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    return mask


def _bar(draw: ImageDraw.ImageDraw, xy: tuple[int, int, int, int], radius: int) -> None:
    draw.rounded_rectangle(xy, radius=radius, fill=(255, 255, 255, 255))


def _draw_corners(draw: ImageDraw.ImageDraw, scale: int) -> None:
    w = 56
    r = 12 * scale

    def s(x: int) -> int:
        return x * scale

    # top-left
    _bar(draw, (s(260), s(285), s(420), s(285 + w)), r)
    _bar(draw, (s(260), s(285), s(260 + w), s(445)), r)
    # top-right
    _bar(draw, (s(604), s(285), s(764), s(285 + w)), r)
    _bar(draw, (s(764 - w), s(285), s(764), s(445)), r)
    # bottom-left
    _bar(draw, (s(260), s(683), s(420), s(683 + w)), r)
    _bar(draw, (s(260), s(523), s(260 + w), s(739)), r)
    # bottom-right
    _bar(draw, (s(604), s(683), s(764), s(683 + w)), r)
    _bar(draw, (s(764 - w), s(523), s(764), s(739)), r)


def _draw_nib(draw: ImageDraw.ImageDraw, scale: int) -> None:
    points = [
        (456, 740),
        (434, 620),
        (386, 540),
        (432, 384),
        (540, 278),
        (668, 238),
        (650, 384),
        (552, 486),
        (642, 450),
        (606, 548),
        (638, 620),
        (572, 700),
        (552, 740),
    ]
    draw.polygon([(x * scale, y * scale) for x, y in points], fill=(255, 255, 255, 255))
    draw.rounded_rectangle(
        (454 * scale, 736 * scale, 570 * scale, 784 * scale),
        radius=8 * scale,
        fill=(255, 255, 255, 255),
    )
    draw.ellipse(
        (494 * scale, 520 * scale, 542 * scale, 568 * scale),
        fill=tuple(round((TOP[i] + BOTTOM[i]) / 2) for i in range(3)) + (255,),
    )
    draw.rectangle(
        (510 * scale, 568 * scale, 526 * scale, 738 * scale),
        fill=tuple(round((TOP[i] + BOTTOM[i]) / 2) for i in range(3)) + (255,),
    )


def render(out: Path) -> None:
    big = SIZE * SS
    canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))

    m = MARGIN * SS
    tile_w = tile_h = big - 2 * m
    tile = _vertical_gradient(tile_w, tile_h).convert("RGBA")
    tile.putalpha(_rounded_mask(tile_w, tile_h, RADIUS * SS))
    canvas.alpha_composite(tile, (m, m))

    draw = ImageDraw.Draw(canvas)
    _draw_corners(draw, SS)
    _draw_nib(draw, SS)

    canvas.resize((SIZE, SIZE), Image.LANCZOS).save(out)
    print(f"wrote {out}")


if __name__ == "__main__":
    render(Path(__file__).with_name("icon.png"))
