# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Wayland screen capture via xdg-desktop-portal.

Wayland refuses out-of-band screen grabs by design, so ``QScreen.grabWindow``
returns nothing there (that is the X11-only slice in :mod:`shotquill.capture.qtgrab`).
The compositor-blessed path is xdg-desktop-portal: we ask
``org.freedesktop.portal.Screenshot`` for a frame and it hands back a PNG on
disk, brokered by the desktop (the user may see a one-time permission prompt).
That is exactly the primitive a screenshot tool wants — far simpler than the
ScreenCast/pipewire route, which is overkill for a single still.

Talking to the portal needs nothing beyond PySide6's QtDBus (already a
dependency). Window enumeration/picking stays unsupported: Wayland does not let
an app enumerate other apps' windows, so ``list_windows``/``capture_window``
raise :class:`CapabilityUnsupported`, same as the X11 slice.

Only :meth:`PortalScreenCapturer._request_screenshot_uri` needs a live portal
(a real Wayland session), so it is isolated as the seam tests replace; the image
loading, region cropping, and error mapping around it are covered by feeding a
known PNG. The QtDBus round-trip itself still needs a real-Wayland smoke.
"""

from __future__ import annotations

import uuid

from shotquill.capture.base import CaptureResult, Rect, ScreenCapturer, WindowInfo
from shotquill.capture.qtgrab import _qimage_to_result
from shotquill.headless import CapabilityUnsupported

_PORTAL_SERVICE = "org.freedesktop.portal.Desktop"
_PORTAL_PATH = "/org/freedesktop/portal/desktop"
_SCREENSHOT_IFACE = "org.freedesktop.portal.Screenshot"
_REQUEST_IFACE = "org.freedesktop.portal.Request"
# The portal may surface a permission or area-selection prompt, so allow the
# user real time to respond before giving up.
_PORTAL_TIMEOUT_MS = 30_000


def _ensure_gui_app() -> None:
    """Create a QGuiApplication if none exists; needed for QImage and the QtDBus
    event loop. Unlike the X11 grab path we never refuse Wayland — the portal is
    precisely how Wayland is meant to be captured."""
    from PySide6.QtGui import QGuiApplication

    if QGuiApplication.instance() is None:
        # Qt keeps its own reference once constructed; we only need it to exist.
        QGuiApplication([])


class PortalScreenCapturer(ScreenCapturer):
    """Full-screen / region capture through xdg-desktop-portal (Wayland)."""

    def __init__(self, include_cursor: bool = False) -> None:
        # The portal decides whether the cursor is drawn; keep the flag for the
        # ScreenCapturer contract (include_cursor is documented best-effort).
        self.include_cursor = include_cursor
        _ensure_gui_app()

    def capture_fullscreen(self, exclude_window_ids: frozenset[int] = frozenset()) -> CaptureResult:
        # The portal cannot omit specific windows, so exclude ids are ignored
        # here (the caller redacts by rectangle); accept them for the interface.
        image, origin, scale = self._grab()
        return _qimage_to_result(image, scale, origin=origin)

    def capture_region(self, region: Rect) -> CaptureResult:
        from PySide6.QtCore import QRect

        # Wayland hands back the whole frame; cropping happens locally on the
        # pixels we already hold (the compositor won't crop for us out of band).
        image, origin, scale = self._grab()
        crop = QRect(
            int((region.x - origin[0]) * scale),
            int((region.y - origin[1]) * scale),
            int(region.width * scale),
            int(region.height * scale),
        )
        if not crop.intersects(image.rect()):
            raise ValueError(f"region {region} is outside the captured screen")
        return _qimage_to_result(
            image.copy(crop.intersected(image.rect())), scale, origin=(region.x, region.y)
        )

    def list_windows(self) -> list[WindowInfo]:
        raise CapabilityUnsupported(
            "list_windows", "Wayland does not allow enumerating other apps' windows"
        )

    def capture_window(self, window_id: int) -> CaptureResult:
        raise CapabilityUnsupported(
            "capture_window", "Wayland does not allow capturing another app's window directly"
        )

    def _grab(self):
        """Portal screenshot → ``(QImage, (origin_x, origin_y), scale)``."""
        from PySide6.QtGui import QImage

        uri = self._request_screenshot_uri(interactive=False)
        path = _uri_to_path(uri)
        image = QImage(path)
        if image.isNull():
            raise CapabilityUnsupported("capture", f"portal returned an unreadable image: {uri!r}")
        image = image.convertToFormat(QImage.Format.Format_RGBA8888)
        origin, scale = self._geometry(image.width())
        return image, origin, scale

    @staticmethod
    def _geometry(physical_width: int):
        """Virtual-desktop origin and the physical/logical scale.

        Derived from Qt's screen list where a session exists; falls back to a
        ``(0, 0)`` origin at scale ``1.0`` (e.g. the offscreen platform in tests,
        or a portal image smaller than the logical desktop), where the image is
        taken at face value. HiDPI scale on Wayland is best-effort here and is
        one of the things the real-Wayland smoke pins down."""
        from PySide6.QtGui import QGuiApplication

        screens = QGuiApplication.screens()
        if not screens:
            return (0, 0), 1.0
        virtual = screens[0].virtualGeometry()
        logical_w = virtual.width()
        if logical_w and physical_width >= logical_w:
            return (virtual.x(), virtual.y()), physical_width / logical_w
        return (virtual.x(), virtual.y()), 1.0

    def _request_screenshot_uri(self, interactive: bool) -> str:
        """Ask the Screenshot portal for a frame; return the ``file://`` URI it
        saved.

        This is the only part that needs a live portal, so it is the seam tests
        replace. It drives a ``QEventLoop`` until the portal's ``Response`` signal
        arrives (the portal may first show a permission or area-selection prompt),
        then returns the ``uri`` result or raises :class:`CapabilityUnsupported`
        on cancel, timeout, or an unreachable portal. The QtDBus round-trip needs
        a real Wayland session to exercise.
        """
        from PySide6.QtCore import QEventLoop, QTimer
        from PySide6.QtDBus import QDBusConnection, QDBusInterface

        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            raise CapabilityUnsupported("capture", "no D-Bus session bus for the portal")
        iface = QDBusInterface(_PORTAL_SERVICE, _PORTAL_PATH, _SCREENSHOT_IFACE, bus)
        if not iface.isValid():
            raise CapabilityUnsupported("capture", "xdg-desktop-portal Screenshot is unavailable")

        token = "shotquill_" + uuid.uuid4().hex
        options = {"handle_token": token, "interactive": bool(interactive), "modal": False}
        reply = iface.call("Screenshot", "", options)
        args = reply.arguments()
        if not args:
            raise CapabilityUnsupported(
                "capture", f"portal rejected the request: {reply.errorMessage()}"
            )
        request_path = args[0]

        result: dict = {}
        loop = QEventLoop()

        def _on_response(message) -> None:
            # Request.Response(u response, a{sv} results): 0 = success.
            payload = message.arguments()
            result["response"] = payload[0] if payload else 2
            result["results"] = payload[1] if len(payload) > 1 else {}
            loop.quit()

        bus.connect(_PORTAL_SERVICE, request_path, _REQUEST_IFACE, "Response", _on_response)
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(_PORTAL_TIMEOUT_MS)
        loop.exec()
        bus.disconnect(_PORTAL_SERVICE, request_path, _REQUEST_IFACE, "Response", _on_response)

        if "response" not in result:
            raise CapabilityUnsupported("capture", "portal did not respond in time")
        if result["response"] != 0:
            raise CapabilityUnsupported("capture", "the screenshot was cancelled")
        uri = (result.get("results") or {}).get("uri")
        if not uri:
            raise CapabilityUnsupported("capture", "portal returned no image URI")
        return str(uri)


def portal_available() -> bool:
    """Best-effort: is the Screenshot portal reachable on the session bus?

    ``squill doctor`` uses this to catch a Wayland box with no xdg-desktop-portal
    installed *before* a capture fails at runtime. Any failure — no session bus,
    no portal service — reads as unavailable rather than raising."""
    try:
        from PySide6.QtDBus import QDBusConnection, QDBusInterface

        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return False
        return bool(QDBusInterface(_PORTAL_SERVICE, _PORTAL_PATH, _SCREENSHOT_IFACE, bus).isValid())
    except Exception:
        return False


def _uri_to_path(uri: str) -> str:
    """Local filesystem path for a portal ``file://`` URI (or a bare path)."""
    from PySide6.QtCore import QUrl

    if uri.startswith("file://"):
        return QUrl(uri).toLocalFile()
    return uri
