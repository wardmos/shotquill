# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
from PIL import Image

from shotquill.capture.base import CaptureResult
from shotquill.output.saver import save


def _red_2x2() -> CaptureResult:
    pixels = bytes([255, 0, 0, 255] * 4)
    return CaptureResult(width=2, height=2, scale=1.0, pixels=pixels)


def test_save_png(tmp_path):
    path = save(_red_2x2(), str(tmp_path), "png")
    assert path.exists()
    assert path.suffix == ".png"
    assert path.name.startswith("Shotquill ")
    with Image.open(path) as img:
        assert img.size == (2, 2)
        assert img.getpixel((0, 0)) == (255, 0, 0, 255)


def test_save_jpg_converts_to_rgb(tmp_path):
    path = save(_red_2x2(), str(tmp_path), "jpg")
    assert path.suffix == ".jpg"
    with Image.open(path) as img:
        assert img.mode == "RGB"


def test_save_creates_missing_directory(tmp_path):
    target = tmp_path / "nested" / "dir"
    path = save(_red_2x2(), str(target), "png")
    assert path.parent == target
    assert path.exists()
