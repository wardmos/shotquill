# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Caller dynamic-mask tests (D14): black out a rectangle before output.

The pure fill maths needs no screen; the CLI/MCP sections capture a known solid
frame through a fake capturer, then decode the result and check the masked
pixels are black while the rest survive.
"""

from __future__ import annotations

import base64
import io
import json

import pytest

from shotquill import audit, cli, headless, mcp, paths
from shotquill.capture.base import CaptureResult, DisplayInfo, Rect, WindowInfo

_GRAY = (200, 200, 200, 255)


def _px(result: CaptureResult, x: int, y: int) -> tuple:
    i = (y * result.width + x) * 4
    return tuple(result.pixels[i : i + 4])


# --- pure: headless.apply_masks ---------------------------------------------


def test_apply_masks_fills_rect_and_spares_the_rest():
    result = CaptureResult(width=4, height=4, scale=1.0, pixels=bytes(_GRAY) * 16)
    masked = headless.apply_masks(result, [Rect(1, 1, 2, 2)])
    assert _px(masked, 1, 1) == (0, 0, 0, 255)
    assert _px(masked, 2, 2) == (0, 0, 0, 255)
    assert _px(masked, 0, 0) == _GRAY  # outside the mask, untouched
    assert _px(masked, 3, 3) == _GRAY


def test_apply_masks_uses_logical_coords_scaled():
    # scale 2: a 1x1 logical mask at (1,1) covers the 2x2 pixel block (2,2)-(4,4).
    result = CaptureResult(width=8, height=8, scale=2.0, pixels=bytes(_GRAY) * 64)
    masked = headless.apply_masks(result, [Rect(1, 1, 1, 1)])
    assert _px(masked, 2, 2) == (0, 0, 0, 255)
    assert _px(masked, 3, 3) == (0, 0, 0, 255)
    assert _px(masked, 1, 1) == _GRAY  # below the scaled origin


def test_apply_masks_empty_is_identity():
    result = CaptureResult(width=2, height=2, scale=1.0, pixels=bytes(_GRAY) * 4)
    assert headless.apply_masks(result, []) is result


# --- CLI / MCP integration --------------------------------------------------


def _gray_result() -> CaptureResult:
    return CaptureResult(width=10, height=10, scale=1.0, pixels=bytes(_GRAY) * 100)


class FakeCapturer:
    include_cursor = False
    windows = [WindowInfo(window_id=1, owner="App", title="T", bounds=Rect(0, 0, 100, 100))]
    displays = [DisplayInfo(index=0, name="d", bounds=Rect(0, 0, 100, 100), primary=True)]

    def capture_fullscreen(self, exclude_window_ids=frozenset()):
        return _gray_result()

    def capture_region(self, region):
        return _gray_result()

    def capture_window(self, window_id):
        return _gray_result()

    def list_windows(self):
        return self.windows

    def list_displays(self):
        return self.displays


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    from shotquill import blocklist as bl

    monkeypatch.setattr(paths, "records_dir", lambda: tmp_path / "records")
    monkeypatch.setattr(paths, "audit_log_path", lambda: tmp_path / "audit.log")
    monkeypatch.setattr(paths, "capture_tmp_dir", lambda: tmp_path / "cap")
    monkeypatch.setattr(audit, "_to_system_log", lambda line: None)
    monkeypatch.setattr(audit, "_caller_chain", lambda: ["pytest"])
    monkeypatch.setattr(headless, "active_blocklist", lambda: bl.Blocklist(()))


@pytest.fixture
def fake_capturer(monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setattr(headless, "get_capturer", lambda include_cursor=False: FakeCapturer())


def _black(image, x: int, y: int) -> bool:
    c = image.pixelColor(x, y)
    return (c.red(), c.green(), c.blue()) == (0, 0, 0)


def _load(path: str):
    from PySide6.QtGui import QImage

    img = QImage()
    assert img.load(path)
    return img


def test_cli_capture_mask_blacks_out_region(fake_capturer, capsys, tmp_path):
    out = str(tmp_path / "shot.png")
    assert cli.main(["capture", "--mask", "2,2,3,3", "-o", out]) == 0
    img = _load(out)
    assert _black(img, 3, 3)  # inside the mask
    assert not _black(img, 0, 0)  # gray elsewhere


def test_cli_capture_bad_mask_is_usage_error(fake_capturer, capsys):
    assert cli.main(["capture", "--mask", "1,2,3"]) == 2
    assert "--mask" in capsys.readouterr().err


def test_cli_record_frame_mask_files_masked_pixels(fake_capturer, capsys, tmp_path):
    cli.main(["record", "start", "--id", "conv-mask"])
    capsys.readouterr()
    rc = cli.main(["record", "frame", "--session", "conv-mask", "--tool", "x", "--mask", "0,0,4,4"])
    assert rc == 0
    img = _load(str(tmp_path / "records" / "conv-mask" / "frames" / "0001.png"))
    assert _black(img, 1, 1)  # inside the mask
    assert not _black(img, 8, 8)  # gray elsewhere


def _mcp_capture(arguments: dict) -> dict:
    raw = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "capture", "arguments": arguments},
        }
    )
    fout = io.StringIO()
    mcp.serve(stdin=io.StringIO(raw + "\n"), stdout=fout)
    return json.loads(fout.getvalue())["result"]


def test_mcp_capture_mask_blacks_out_region(fake_capturer):
    from PySide6.QtGui import QImage

    result = _mcp_capture({"mask": [{"x": 2, "y": 2, "width": 4, "height": 4}]})
    data = base64.b64decode(result["content"][0]["data"])
    img = QImage.fromData(data)
    assert (img.pixelColor(3, 3).red(), img.pixelColor(3, 3).green()) == (0, 0)
    assert img.pixelColor(0, 0).red() == 200


def test_mcp_capture_bad_mask_is_invalid_arguments(fake_capturer):
    result = _mcp_capture({"mask": [{"x": 1, "y": 2}]})
    assert result["isError"] is True
    assert json.loads(result["content"][0]["text"])["type"] == "invalid_arguments"
