# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Reveal / blur-everything-else tests (D15 layer 4).

`pixelate_except` mosaics the whole frame and keeps only the revealed
rectangles sharp. The pure section builds a QImage with a known sharp feature
and checks the revealed region survives while the rest is averaged away; the
CLI/MCP sections drive a capturer whose frame has a lone white pixel.
"""

from __future__ import annotations

import base64
import io
import json

import pytest

from shotquill import audit, cli, headless, mcp, paths
from shotquill.capture.base import CaptureResult, DisplayInfo, Rect, WindowInfo

pytest.importorskip("PySide6")

_GRAY = (200, 200, 200, 255)


# --- pure: imaging.pixelate_except ------------------------------------------


def _gray_qimage():
    from PySide6.QtGui import QColor, QImage

    img = QImage(48, 48, QImage.Format.Format_RGBA8888)
    img.fill(QColor(200, 200, 200))
    img.setPixelColor(5, 5, QColor(255, 255, 255))  # a lone sharp pixel, outside reveal
    for x in range(20, 28):  # a blue block inside the reveal region
        for y in range(20, 28):
            img.setPixelColor(x, y, QColor(0, 0, 255))
    return img


def test_pixelate_except_empty_is_identity():
    from shotquill.imaging import pixelate_except

    img = _gray_qimage()
    assert pixelate_except(img, [], 1.0) is img


def test_pixelate_except_keeps_reveal_sharp_blurs_rest():
    from shotquill.imaging import pixelate_except

    out = pixelate_except(_gray_qimage(), [Rect(20, 20, 8, 8)], 1.0)
    # The revealed block survives verbatim.
    blue = out.pixelColor(22, 22)
    assert (blue.red(), blue.green(), blue.blue()) == (0, 0, 255)
    # The lone white pixel outside the reveal is averaged into its mosaic cell.
    white = out.pixelColor(5, 5)
    assert white.red() < 255


# --- CLI / MCP integration --------------------------------------------------


def _spotted_result() -> CaptureResult:
    buf = bytearray(bytes(_GRAY) * (48 * 48))
    i = (5 * 48 + 5) * 4  # one white pixel at (5, 5)
    buf[i : i + 4] = bytes((255, 255, 255, 255))
    return CaptureResult(width=48, height=48, scale=1.0, pixels=bytes(buf))


class FakeCapturer:
    include_cursor = False
    windows = [WindowInfo(window_id=1, owner="App", title="T", bounds=Rect(0, 0, 100, 100))]
    displays = [DisplayInfo(index=0, name="d", bounds=Rect(0, 0, 100, 100), primary=True)]

    def capture_fullscreen(self, exclude_window_ids=frozenset()):
        return _spotted_result()

    def capture_region(self, region):
        return _spotted_result()

    def capture_window(self, window_id):
        return _spotted_result()

    def list_windows(self):
        return self.windows

    def list_displays(self):
        return self.displays


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    from shotquill import blocklist as bl

    monkeypatch.setattr(paths, "audit_log_path", lambda: tmp_path / "audit.log")
    monkeypatch.setattr(paths, "capture_tmp_dir", lambda: tmp_path / "cap")
    monkeypatch.setattr(audit, "_to_system_log", lambda line: None)
    monkeypatch.setattr(audit, "_caller_chain", lambda: ["pytest"])
    monkeypatch.setattr(headless, "active_blocklist", lambda: bl.Blocklist(()))


@pytest.fixture
def fake_capturer(monkeypatch):
    monkeypatch.setattr(headless, "get_capturer", lambda include_cursor=False: FakeCapturer())


def _load(path: str):
    from PySide6.QtGui import QImage

    img = QImage()
    assert img.load(path)
    return img


def test_cli_capture_reveal_keeps_spot(fake_capturer, tmp_path):
    out = str(tmp_path / "shot.png")
    assert cli.main(["capture", "--reveal", "4,4,4,4", "-o", out]) == 0
    img = _load(out)
    assert img.pixelColor(5, 5).red() == 255  # the white pixel is inside the reveal


def test_cli_capture_reveal_elsewhere_blurs_spot(fake_capturer, tmp_path):
    out = str(tmp_path / "shot.png")
    assert cli.main(["capture", "--reveal", "30,30,4,4", "-o", out]) == 0
    img = _load(out)
    assert img.pixelColor(5, 5).red() < 255  # outside the reveal -> averaged away


def test_cli_capture_bad_reveal_is_usage_error(fake_capturer, capsys):
    assert cli.main(["capture", "--reveal", "1,2"]) == 2
    assert "--reveal" in capsys.readouterr().err


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


def test_mcp_capture_reveal_keeps_spot(fake_capturer):
    from PySide6.QtGui import QImage

    result = _mcp_capture({"reveal": [{"x": 4, "y": 4, "width": 4, "height": 4}]})
    img = QImage.fromData(base64.b64decode(result["content"][0]["data"]))
    assert img.pixelColor(5, 5).red() == 255


def test_mcp_capture_bad_reveal_is_invalid_arguments(fake_capturer):
    result = _mcp_capture({"reveal": [{"x": 1}]})
    assert result["isError"] is True
    assert json.loads(result["content"][0]["text"])["type"] == "invalid_arguments"
