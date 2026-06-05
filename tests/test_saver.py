# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
import pytest
from PySide6.QtGui import QImage

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
    img = QImage(str(path))
    assert (img.width(), img.height()) == (2, 2)
    assert img.pixelColor(0, 0).getRgb() == (255, 0, 0, 255)


def test_save_jpg_converts_to_rgb(tmp_path):
    path = save(_red_2x2(), str(tmp_path), "jpg")
    assert path.suffix == ".jpg"
    img = QImage(str(path))
    assert not img.isNull()
    assert not img.hasAlphaChannel()


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


def test_build_output_path_never_reuses_an_existing_name(tmp_path, monkeypatch):
    # The timestamp resolves to seconds, so two captures in the same second
    # must get distinct names instead of silently overwriting each other.
    import datetime as real_dt

    class _FrozenDateTime(real_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 6, 4, 14, 30, 0)

    from shotquill.output import saver

    monkeypatch.setattr(saver.dt, "datetime", _FrozenDateTime)
    first = build_output_path(str(tmp_path), "png")
    first.touch()
    second = build_output_path(str(tmp_path), "png")
    second.touch()
    third = build_output_path(str(tmp_path), "png")
    assert first.name == "ShotQuill 2026-06-04 14.30.00.png"
    assert second.name == "ShotQuill 2026-06-04 14.30.00 (2).png"
    assert third.name == "ShotQuill 2026-06-04 14.30.00 (3).png"


def test_rapid_saves_in_the_same_second_keep_every_file(tmp_path):
    # End-to-end: two back-to-back saves (well within one second) must both
    # survive on disk.
    save(_red_2x2(), str(tmp_path), "png")
    save(_red_2x2(), str(tmp_path), "png")
    assert len(list(tmp_path.glob("ShotQuill *.png"))) == 2


def test_save_unpremultiplies_window_captures(tmp_path):
    # Window captures arrive premultiplied; the saved file must hold straight
    # alpha (half-transparent red premultiplied is (128, 0, 0, 128)).
    pixels = bytes([128, 0, 0, 128] * 4)
    result = CaptureResult(width=2, height=2, scale=1.0, pixels=pixels, premultiplied=True)
    path = save(result, str(tmp_path), "png")
    img = QImage(str(path))
    assert img.pixelColor(0, 0).getRgb() == (255, 0, 0, 128)


def test_save_raises_when_directory_unwritable(tmp_path):
    target = tmp_path / "readonly"
    target.mkdir()
    target.chmod(0o500)
    try:
        with pytest.raises(OSError):
            save(_red_2x2(), str(target), "png")
    finally:
        target.chmod(0o700)


def test_save_qimage_writes_file(tmp_path):
    from PySide6.QtGui import QColor

    from shotquill.output.saver import save_qimage

    image = QImage(5, 4, QImage.Format.Format_ARGB32)
    image.fill(QColor("blue"))
    path = save_qimage(image, str(tmp_path), "png")
    assert path.exists()
    saved = QImage(str(path))
    assert (saved.width(), saved.height()) == (5, 4)
