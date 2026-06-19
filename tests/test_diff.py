# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""`squill diff` — compare two images for a golden-image / before-after check.

The pixel maths lives in imaging.image_diff_box (tested in test_imaging.py); this
covers the CLI contract: the one-line / JSON output and the exit-code band
(0 identical, 20 changed) a CI step branches on.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QColor, QImage  # noqa: E402

from shotquill import cli  # noqa: E402

_EXIT_CHANGED = 20


def _png(path, width=20, height=12, *, box=None, color=(0, 0, 0)):
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(QColor(*color))
    if box:
        x, y, w, h = box
        for px in range(x, x + w):
            for py in range(y, y + h):
                image.setPixelColor(px, py, QColor(255, 0, 0))
    assert image.save(str(path), "PNG")
    return str(path)


def test_identical_images_are_exit_zero(tmp_path, capsys):
    a = _png(tmp_path / "a.png")
    assert cli.main(["diff", a, a]) == 0
    assert capsys.readouterr().out.strip() == "identical"


def test_changed_images_report_box_and_exit_20(tmp_path, capsys):
    a = _png(tmp_path / "a.png")
    b = _png(tmp_path / "b.png", box=(5, 2, 4, 3))
    assert cli.main(["diff", a, b]) == _EXIT_CHANGED
    assert capsys.readouterr().out.strip() == "changed: 5,2,4,3"


def test_json_output(tmp_path, capsys):
    a = _png(tmp_path / "a.png")
    b = _png(tmp_path / "b.png", box=(5, 2, 4, 3))
    assert cli.main(["diff", a, b, "--json"]) == _EXIT_CHANGED
    payload = json.loads(capsys.readouterr().out)
    assert payload["changed"] is True
    assert payload["box"] == {"x": 5, "y": 2, "width": 4, "height": 3}
    assert payload["a_size"] == {"width": 20, "height": 12}


def test_size_mismatch_is_changed_without_a_box(tmp_path, capsys):
    a = _png(tmp_path / "a.png", width=20, height=12)
    b = _png(tmp_path / "b.png", width=10, height=10)
    assert cli.main(["diff", a, b]) == _EXIT_CHANGED
    assert "size 20x12 vs 10x10" in capsys.readouterr().out


def test_size_mismatch_json_has_reason(tmp_path, capsys):
    a = _png(tmp_path / "a.png", width=20, height=12)
    b = _png(tmp_path / "b.png", width=10, height=10)
    cli.main(["diff", a, b, "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["changed"] is True
    assert "box" not in payload
    assert payload["reason"] == "size differs"


def test_threshold_absorbs_small_deltas(tmp_path, capsys):
    a = _png(tmp_path / "a.png", color=(10, 10, 10))
    b = _png(tmp_path / "b.png", color=(10, 10, 10), box=(0, 0, 1, 1))  # one red pixel
    # Exact: differs. A high threshold treats the small change as noise.
    assert cli.main(["diff", a, b]) == _EXIT_CHANGED
    capsys.readouterr()
    assert cli.main(["diff", a, b, "--threshold", "255"]) == 0


def test_unreadable_file_is_error_exit_1(tmp_path, capsys):
    a = _png(tmp_path / "a.png")
    assert cli.main(["diff", a, str(tmp_path / "missing.png")]) == 1
    assert "cannot read" in capsys.readouterr().err


def test_undecodable_file_is_error_exit_1(tmp_path, capsys):
    a = _png(tmp_path / "a.png")
    bogus = tmp_path / "bogus.png"
    bogus.write_bytes(b"not an image")
    assert cli.main(["diff", a, str(bogus)]) == 1
    assert "not a decodable image" in capsys.readouterr().err


def test_both_stdin_is_usage_error(capsys):
    assert cli.main(["diff", "-", "-"]) == 2
    assert "only one" in capsys.readouterr().err
