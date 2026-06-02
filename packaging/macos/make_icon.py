#!/usr/bin/env python3
"""Render the Shotquill app icon master PNG.

Produces a 1024x1024 PNG matching the menu-bar mark (blue rounded tile + white
"S"), following Apple's icon grid (~10% transparent margin around the tile).

The committed ``icon.png`` is what ``build_dmg.sh`` converts into ``.icns`` with
macOS' native ``sips``/``iconutil``, so the build never depends on fonts being
present on the build machine. Re-run this only to regenerate the artwork::

    python3 packaging/macos/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 1024
SS = 4  # supersampling factor for crisp edges / anti-aliasing
MARGIN = 100  # Apple grid: tile sits inside a ~10% margin
RADIUS = 185  # rounded-tile corner radius (squircle-ish)

# Brand blue, as a top->bottom gradient around the menu-bar #2d7ff9.
TOP = (74, 155, 255)
BOTTOM = (31, 111, 224)

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


def _load_font(px: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, px)
    raise SystemExit("No suitable bold sans font found; install Liberation/DejaVu.")


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


def render(out: Path) -> None:
    big = SIZE * SS
    canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))

    m = MARGIN * SS
    tile_w = tile_h = big - 2 * m
    tile = _vertical_gradient(tile_w, tile_h).convert("RGBA")
    tile.putalpha(_rounded_mask(tile_w, tile_h, RADIUS * SS))
    canvas.alpha_composite(tile, (m, m))

    draw = ImageDraw.Draw(canvas)
    font = _load_font(int(560 * SS))
    # Center the glyph on its actual ink bounds, not the font's line box.
    w, h = font.getsize("S")
    ox, oy = font.getoffset("S")
    draw.text(
        ((big - w) / 2 - ox, (big - h) / 2 - oy),
        "S",
        font=font,
        fill=(255, 255, 255, 255),
    )

    canvas.resize((SIZE, SIZE), Image.LANCZOS).save(out)
    print(f"wrote {out}")


if __name__ == "__main__":
    render(Path(__file__).with_name("icon.png"))
