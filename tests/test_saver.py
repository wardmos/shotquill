# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
import pytest
from PIL import Image

from shotquill.capture.base import CaptureResult
from shotquill.output.saver import build_output_path, save


def _red_2x2() -> CaptureResult:
    pixels = bytes([255, 0, 0, 255] * 4)
    return CaptureResult(width=2, height=2, scale=1.0, pixels=pixels)


def test_save_png(tmp_path):
    path = save(_red_2x2(), str(tmp_path), "png")
    assert path.exists()
    assert path.suffix == ".png"
    assert path.name.startswith("ShotQuill ")
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


@pytest.mark.parametrize(
    ("fmt", "expected_ext"),
    [
        ("png", ".png"),
        ("PNG", ".png"),
        ("jpg", ".jpg"),
        ("jpeg", ".jpg"),
        ("JPEG", ".jpg"),
        ("unknown", ".png"),  # anything unrecognized falls back to png
    ],
)
def test_build_output_path_extension(tmp_path, fmt, expected_ext):
    path = build_output_path(str(tmp_path), fmt)
    assert path.suffix == expected_ext
    assert path.name.startswith("ShotQuill ")
    assert path.parent == tmp_path


def test_build_output_path_expands_user(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = build_output_path("~/shots", "png")
    assert str(path).startswith(str(tmp_path))
    assert (tmp_path / "shots").is_dir()


def test_save_qimage_writes_file(tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtGui import QColor, QImage

    from shotquill.output.saver import save_qimage

    image = QImage(5, 4, QImage.Format.Format_ARGB32)
    image.fill(QColor("blue"))
    path = save_qimage(image, str(tmp_path), "png")
    assert path.exists()
    with Image.open(path) as img:
        assert img.size == (5, 4)
