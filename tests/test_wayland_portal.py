# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""xdg-desktop-portal capture backend: everything around the live D-Bus call.

The portal round-trip itself (``_request_screenshot_uri``) needs a real Wayland
session, so it is stubbed here; these cover the image loading, region cropping,
error mapping, and backend routing that surround it.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from shotquill import headless  # noqa: E402
from shotquill.capture.base import Rect  # noqa: E402


def _write_png(path, width, height, rgb=(200, 30, 30)) -> str:
    """A solid PNG of the given size; returns its ``file://`` URI."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QColor, QImage

    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(QColor(*rgb))
    assert image.save(str(path), "PNG")
    return QUrl.fromLocalFile(str(path)).toString()


@pytest.fixture
def capturer(qapp):
    from shotquill.capture.wayland import PortalScreenCapturer

    return PortalScreenCapturer()


def _stub_uri(monkeypatch, capturer, uri):
    monkeypatch.setattr(capturer, "_request_screenshot_uri", lambda interactive=False: uri)


def test_fullscreen_loads_the_portal_png(capturer, monkeypatch, tmp_path):
    uri = _write_png(tmp_path / "shot.png", 8, 6)
    _stub_uri(monkeypatch, capturer, uri)
    result = capturer.capture_fullscreen()
    assert (result.width, result.height) == (8, 6)
    assert len(result.pixels) == 8 * 6 * 4
    assert result.pixels[:4] == bytes((200, 30, 30, 255))  # the fill survives round-trip


def test_fullscreen_reports_geometry_origin_and_scale(capturer, monkeypatch, tmp_path):
    # Geometry comes from the screen list; stub it so the assertion does not
    # depend on the offscreen platform's screen size.
    uri = _write_png(tmp_path / "shot.png", 20, 10)
    _stub_uri(monkeypatch, capturer, uri)
    monkeypatch.setattr(type(capturer), "_geometry", staticmethod(lambda w: ((100, 50), 2.0)))
    result = capturer.capture_fullscreen()
    assert (result.origin_x, result.origin_y) == (100, 50)
    assert result.scale == 2.0


def test_region_is_cropped_locally(capturer, monkeypatch, tmp_path):
    uri = _write_png(tmp_path / "shot.png", 20, 16)
    _stub_uri(monkeypatch, capturer, uri)
    monkeypatch.setattr(type(capturer), "_geometry", staticmethod(lambda w: ((0, 0), 1.0)))
    result = capturer.capture_region(Rect(x=2, y=3, width=10, height=8))
    assert (result.width, result.height) == (10, 8)
    assert (result.origin_x, result.origin_y) == (2, 3)
    assert len(result.pixels) == 10 * 8 * 4


def test_region_outside_screen_is_rejected(capturer, monkeypatch, tmp_path):
    uri = _write_png(tmp_path / "shot.png", 8, 6)
    _stub_uri(monkeypatch, capturer, uri)
    monkeypatch.setattr(type(capturer), "_geometry", staticmethod(lambda w: ((0, 0), 1.0)))
    with pytest.raises(ValueError):
        capturer.capture_region(Rect(x=999, y=999, width=4, height=4))


def test_unreadable_portal_image_is_typed_unsupported(capturer, monkeypatch, tmp_path):
    missing = tmp_path / "gone.png"  # never written → QImage load fails
    _stub_uri(monkeypatch, capturer, f"file://{missing}")
    with pytest.raises(headless.CapabilityUnsupported):
        capturer.capture_fullscreen()


def test_portal_temp_file_is_removed_after_read(capturer, monkeypatch, tmp_path):
    # The portal's on-disk PNG is a plaintext-screen copy; once read into memory
    # it must not linger in scratch space.
    from shotquill.capture import wayland

    shot = tmp_path / "shot.png"
    _stub_uri(monkeypatch, capturer, _write_png(shot, 8, 6))
    monkeypatch.setattr(wayland, "_ephemeral_roots", lambda: [tmp_path])
    result = capturer.capture_fullscreen()
    assert (result.width, result.height) == (8, 6)  # read succeeded
    assert not shot.exists()  # and the file did not linger


def test_portal_temp_file_removed_even_when_image_is_unreadable(capturer, monkeypatch, tmp_path):
    # An undecodable frame is still a plaintext file on disk; clean it up too.
    from shotquill.capture import wayland

    junk = tmp_path / "broken.png"
    junk.write_bytes(b"not a png")
    _stub_uri(monkeypatch, capturer, f"file://{junk}")
    monkeypatch.setattr(wayland, "_ephemeral_roots", lambda: [tmp_path])
    with pytest.raises(headless.CapabilityUnsupported):
        capturer.capture_fullscreen()
    assert not junk.exists()


def test_portal_file_outside_ephemeral_root_is_kept(capturer, monkeypatch, tmp_path):
    # A backend that routes the screenshot into the user's own folder owns that
    # file — never delete it.
    from shotquill.capture import wayland

    pictures = tmp_path / "Pictures"
    pictures.mkdir()
    shot = pictures / "Screenshot.png"
    _stub_uri(monkeypatch, capturer, _write_png(shot, 8, 6))
    monkeypatch.setattr(wayland, "_ephemeral_roots", lambda: [tmp_path / "ephemeral"])
    capturer.capture_fullscreen()
    assert shot.exists()


def test_portal_failure_propagates(capturer, monkeypatch):
    def _boom(interactive=False):
        raise headless.CapabilityUnsupported("capture", "the screenshot was cancelled")

    monkeypatch.setattr(capturer, "_request_screenshot_uri", _boom)
    with pytest.raises(headless.CapabilityUnsupported):
        capturer.capture_fullscreen()


def test_window_operations_are_typed_unsupported(capturer):
    with pytest.raises(headless.CapabilityUnsupported):
        capturer.list_windows()
    with pytest.raises(headless.CapabilityUnsupported):
        capturer.capture_window(1)


def test_uri_to_path_handles_file_uri_and_bare_path():
    from shotquill.capture.wayland import _uri_to_path

    assert _uri_to_path("file:///tmp/a.png") == "/tmp/a.png"
    assert _uri_to_path("/tmp/b.png") == "/tmp/b.png"


def test_request_handle_path_derives_per_portal_spec():
    # Subscribing to Response before the call needs the request path computed up
    # front: the unique name loses its leading ':' and every '.' becomes '_'.
    from shotquill.capture.wayland import _request_handle_path

    assert (
        _request_handle_path(":1.42", "shotquill_abc")
        == "/org/freedesktop/portal/desktop/request/1_42/shotquill_abc"
    )
    # An already-sanitized name (no leading colon) only gets the dot swap.
    assert _request_handle_path("1.512", "t") == "/org/freedesktop/portal/desktop/request/1_512/t"


def test_wayland_session_detection(monkeypatch):
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert headless._is_wayland_session() is True
    # An explicit QPA platform (offscreen/xcb) keeps the Qt-grab path selectable.
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    assert headless._is_wayland_session() is False


def test_get_capturer_routes_wayland_to_portal(monkeypatch):
    monkeypatch.setattr(headless.sys, "platform", "linux")
    monkeypatch.setattr(headless, "_is_wayland_session", lambda: True)
    from shotquill.capture.wayland import PortalScreenCapturer

    assert isinstance(headless.get_capturer(), PortalScreenCapturer)


def test_get_capturer_routes_x11_to_qtgrab(monkeypatch):
    monkeypatch.setattr(headless.sys, "platform", "linux")
    monkeypatch.setattr(headless, "_is_wayland_session", lambda: False)
    from shotquill.capture.qtgrab import QtGrabCapturer

    assert isinstance(headless.get_capturer(), QtGrabCapturer)


def test_portal_available_returns_a_bool_without_raising():
    from shotquill.capture import wayland

    assert isinstance(wayland.portal_available(), bool)  # never raises, even with no bus


def test_doctor_capture_detail_reports_reachable_portal(monkeypatch):
    from shotquill.capture import wayland

    monkeypatch.setattr(headless.sys, "platform", "linux")
    monkeypatch.setattr(headless, "_is_wayland_session", lambda: True)
    monkeypatch.setattr(wayland, "portal_available", lambda: True)
    detail, available = headless._capture_detail("PortalScreenCapturer")
    assert available is True
    assert "portal" in detail.lower()


def test_doctor_capture_detail_flags_unreachable_portal(monkeypatch):
    from shotquill.capture import wayland

    monkeypatch.setattr(headless.sys, "platform", "linux")
    monkeypatch.setattr(headless, "_is_wayland_session", lambda: True)
    monkeypatch.setattr(wayland, "portal_available", lambda: False)
    detail, available = headless._capture_detail("PortalScreenCapturer")
    assert available is False
    assert "install xdg-desktop-portal" in detail  # actionable, not a bare class name
