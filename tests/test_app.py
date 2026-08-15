# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Headless tests for the top-level application controller.

The platform managers (screen capture, global hotkeys, launch-at-login) are
replaced with fakes (the shared ``fakes`` fixture in conftest) so the
orchestration logic — hotkey registration, the capture success/failure paths,
autostart syncing, window bookkeeping — can be exercised offscreen without
touching real system frameworks.
"""

import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QProcess, Qt, Signal  # noqa: E402
from PySide6.QtWidgets import QDialog  # noqa: E402

from shotquill import app as app_module  # noqa: E402
from shotquill import blocklist as bl  # noqa: E402
from shotquill import debug_log, paths  # noqa: E402
from shotquill.capture.base import CaptureResult, Rect, WindowInfo  # noqa: E402
from shotquill.permissions import PermissionStatus  # noqa: E402


def _build_app(qapp, fakes):
    return app_module.ShotquillApp(qapp)


def test_build_icon_is_not_null(qapp):
    icon = app_module._build_icon()
    assert not icon.isNull()


def test_build_icon_attaches_multiple_sizes_on_linux(qapp, monkeypatch):
    # A single 64px pixmap renders soft on a HiDPI panel (Qt has to downscale
    # per frame) and small on a standard panel. Confirm the Linux branch hands
    # Qt a multi-resolution icon so it can pick the right one per panel.
    monkeypatch.setattr(app_module.sys, "platform", "linux")
    icon = app_module._build_icon()
    sizes = {(s.width(), s.height()) for s in icon.availableSizes()}
    # The actual list lives in _build_icon; assert the small/large extremes
    # are there so it doesn't silently collapse back to a single size.
    assert (16, 16) in sizes
    assert (64, 64) in sizes
    assert len(sizes) >= 4


def test_build_icon_marks_macos_template(qapp, monkeypatch):
    # macOS reads the tray icon as a *template* (only alpha matters; the menu
    # bar tints opaque pixels white-on-dark / dark-on-light). Without the mask
    # flag the icon would render as raw black pixels, which is wrong in both
    # light and dark menu bars.
    monkeypatch.setattr(app_module.sys, "platform", "darwin")
    icon = app_module._build_icon()
    assert icon.isMask() is True


def test_build_icon_does_not_mark_template_off_macos(qapp, monkeypatch):
    # Non-Mac desktops (Linux/X11 tray) don't tint masks: marking the icon as
    # a template here would leave a black tile with transparent glyphs — i.e.
    # the very bug the multi-size Linux branch was added to avoid.
    monkeypatch.setattr(app_module.sys, "platform", "linux")
    icon = app_module._build_icon()
    assert icon.isMask() is False


def test_render_tray_pixmap_keeps_glyph_legible_at_small_sizes(qapp):
    # The renderer derives padding / radius / glyph height from ``size`` so
    # small tray panels still get a readable mark instead of a near-empty tile.
    # Check the extremes the icon factory actually asks for (16 and 64) and
    # confirm each produces a non-empty pixmap with some opaque pixels.
    from PySide6.QtCore import Qt

    for size in (16, 64):
        pixmap = app_module._render_tray_pixmap(size, is_mac=False)
        assert pixmap.size().width() == size
        assert pixmap.size().height() == size
        # Sample the centre pixel: the mark sits there, so the alpha must be
        # non-zero. (Fully transparent centre would mean either the tile or the
        # glyph went missing.)
        centre = pixmap.toImage().pixelColor(size // 2, size // 2)
        assert centre.alpha() > 0
        # And not the placeholder transparent fill.
        assert centre != Qt.transparent


def test_render_tray_pixmap_linux_paints_glyph_white(qapp):
    # The Linux/X11 tray path can't tint masks, so the mark must be painted
    # directly — verify by scanning for any white-ish pixel. A regression
    # that left the glyph unpainted (or painted it black on black) would
    # leave only a featureless black tile that's invisible against a dark
    # panel.
    pixmap = app_module._render_tray_pixmap(64, is_mac=False)
    image = pixmap.toImage()
    found_white = False
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            # Antialiasing softens edges, so accept anything that's clearly
            # on the white-of-glyph side — alpha > 0 AND the average channel
            # value is past mid-grey.
            if pixel.alpha() > 0 and (pixel.red() + pixel.green() + pixel.blue()) / 3 > 200:
                found_white = True
                break
        if found_white:
            break
    assert found_white, "expected the tray glyph to be painted white somewhere on the tile"


def test_render_tray_pixmap_linux_keeps_mark_inside_viewfinder(qapp):
    pixmap = app_module._render_tray_pixmap(64, is_mac=False)
    image = pixmap.toImage()
    white_pixels = []
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() > 0 and (pixel.red() + pixel.green() + pixel.blue()) / 3 > 200:
                white_pixels.append((x, y))
    assert white_pixels
    _left, bottom = max(white_pixels, key=lambda point: point[1])
    assert bottom <= 49


def test_render_tray_pixmap_linux_keeps_central_mark_compact(qapp):
    pixmap = app_module._render_tray_pixmap(64, is_mac=False)
    image = pixmap.toImage()
    centre_pixels = []
    for y in range(image.height()):
        for x in range(31, 35):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() > 0 and (pixel.red() + pixel.green() + pixel.blue()) / 3 > 200:
                centre_pixels.append((x, y))
    assert centre_pixels
    top = min(y for _x, y in centre_pixels)
    bottom = max(y for _x, y in centre_pixels)
    assert top >= 25
    assert bottom <= 47


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="Qt offscreen glyph rasterization on Windows doesn't punch the template hole; "
    "the macOS template path is exercised on macOS/Linux",
)
def test_render_tray_pixmap_macos_uses_inverted_template_mask(qapp):
    # macOS reads the icon as a template: opaque pixels become the status-bar
    # glyph, transparent pixels disappear. The macOS path should therefore draw
    # the capture/pen mark itself, not an opaque rounded tile with the mark cut
    # out of it.
    pixmap = app_module._render_tray_pixmap(64, is_mac=True)
    image = pixmap.toImage()
    opaque = []
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() > 0:
                opaque.append((x, y))
                assert (pixel.red(), pixel.green(), pixel.blue()) == (0, 0, 0), (
                    "macOS template path must paint only black; found "
                    f"({pixel.red()},{pixel.green()},{pixel.blue()}) at ({x},{y})"
                )
    assert opaque
    left, top = min(x for x, _y in opaque), min(y for _x, y in opaque)
    right, bottom = max(x for x, _y in opaque), max(y for _x, y in opaque)
    assert 4 <= left <= 8
    assert 6 <= top <= 10
    assert 55 <= right <= 59
    assert 53 <= bottom <= 57
    assert image.pixelColor(32, 32).alpha() > 0
    assert image.pixelColor(2, 2).alpha() == 0


def test_render_tray_pixmap_keeps_glyph_inside_tile_bounds(qapp):
    # At extreme small sizes the derived font.pixelSize could in principle
    # produce a glyph taller than the rounded tile, which would leak opaque
    # pixels into the padding strip (outside the tile but inside the pixmap).
    # That would render as a stray dark splotch on a panel that expects a
    # clean rounded square. Scan the outermost row/column of the pixmap and
    # confirm everything there is transparent — i.e. no glyph overflow.
    for size in (16, 22, 24, 32, 48, 64):
        pixmap = app_module._render_tray_pixmap(size, is_mac=False)
        image = pixmap.toImage()
        edges = [(x, 0) for x in range(size)] + [(x, size - 1) for x in range(size)]
        edges += [(0, y) for y in range(size)] + [(size - 1, y) for y in range(size)]
        for x, y in edges:
            assert image.pixelColor(x, y).alpha() == 0, (
                f"glyph or tile leaked to the pixmap edge at size={size}, ({x},{y})"
            )


def _stub_run_environment(qapp, monkeypatch):
    """Shared monkeypatching for the ``run()`` tray-unavailable tests."""
    monkeypatch.setattr(app_module.QSystemTrayIcon, "isSystemTrayAvailable", lambda: False)
    # Stub QApplication so run() doesn't try to create a second one. The
    # qapp fixture already gave us a live QApplication.
    monkeypatch.setattr(app_module, "QApplication", lambda argv: qapp)
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(
        app_module.QMessageBox,
        "critical",
        lambda parent, title, body: shown.append((title, body)),
    )
    return shown


def test_run_shows_dialog_when_tray_unavailable(qapp, monkeypatch):
    # The previous behaviour printed one stderr line and returned 1 — a user
    # double-clicking the desktop entry on GNOME 42+ (no legacy tray) saw
    # nothing happen. ``run`` must now also pop a QMessageBox so the failure
    # is visible to GUI users; the stderr line stays for CLI invocations.
    from shotquill import i18n

    shown = _stub_run_environment(qapp, monkeypatch)

    rc = app_module.run()

    assert rc == 1
    assert len(shown) == 1
    title, body = shown[0]
    assert title == i18n.t("tray.unavailable_title")
    # Body is the platform-appropriate one — Linux mentions AppIndicator;
    # other platforms get the generic body. Either way it's non-empty.
    assert body


def test_run_dialog_body_is_linux_specific_on_linux(qapp, monkeypatch):
    # Lock in the platform branch: on Linux the body must mention the
    # AppIndicator extension — the actionable hint for the common GNOME 42+
    # stumble. Translations should never drop this token; if they do,
    # ``test_every_string_has_all_languages`` still passes but the user gets
    # a body that doesn't tell them what to do.
    from shotquill import i18n

    monkeypatch.setattr(i18n.sys, "platform", "linux")
    shown = _stub_run_environment(qapp, monkeypatch)

    app_module.run()

    _, body = shown[0]
    assert body == i18n.t("tray.unavailable_body_linux")
    assert "AppIndicator" in body
    # Don't drop the "CLI/MCP still works" escape hatch in a translation —
    # this is the only way a tray-less Linux user knows they aren't blocked.
    assert "squill" in body


def test_run_dialog_body_is_generic_off_linux(qapp, monkeypatch):
    # Off-Linux falls through to the generic body — there's no equivalent
    # to the GNOME 42+ tray-removed story on macOS/Windows, so the message
    # stays short. The platform branch lives in ``tray_unavailable_body_key``
    # rather than in ``run``, so this guards both call sites at once.
    from shotquill import i18n

    monkeypatch.setattr(i18n.sys, "platform", "darwin")
    shown = _stub_run_environment(qapp, monkeypatch)

    app_module.run()

    _, body = shown[0]
    assert body == i18n.t("tray.unavailable_body_generic")
    assert "squill" in body  # CLI/MCP escape hatch stays in the body


def test_app_is_qobject_for_queued_hotkey_delivery(qapp, config, fakes):
    # ShotquillApp must be a QObject so the hotkey bridge's signals — emitted
    # from pynput's listener thread — reach the capture slots via a *queued*
    # connection onto the GUI thread. A plain-object receiver makes Qt fall back
    # to a direct call on the listener thread, where building the overlay/editor
    # QWidgets crashes on macOS and the hotkey appears dead (menu still works).
    from PySide6.QtCore import QObject

    app = _build_app(qapp, fakes)
    assert isinstance(app, QObject)
    app.shutdown()


def test_apply_hotkeys_registers_all_capture_combos(qapp, config, fakes):
    _capturer, hotkeys, _autostart = fakes
    app = _build_app(qapp, fakes)
    assert set(hotkeys.bindings) == {"<alt>+a", "<alt>+s"}
    assert hotkeys.started >= 1
    app.shutdown()


def test_apply_hotkeys_skips_disabled_combos(qapp, config, fakes):
    config.set_hotkey_enabled("fullscreen_capture", False)
    _capturer, hotkeys, _autostart = fakes
    app = _build_app(qapp, fakes)
    # The disabled fullscreen combo is never registered with the listener.
    assert set(hotkeys.bindings) == {"<alt>+a"}
    app.shutdown()


def test_apply_hotkeys_opens_input_monitoring_when_permission_missing(
    qapp, config, fakes, monkeypatch
):
    # On macOS, a denied Input Monitoring grant raises PermissionError; the app
    # must both notify the user AND open the right System Settings pane (so the
    # user has a one-tap path to fix it without hunting through preferences).
    monkeypatch.setattr(app_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        app_module.permissions, "screen_capture_status", lambda: PermissionStatus.GRANTED
    )
    _capturer, hotkeys, _autostart = fakes
    hotkeys.raise_permission_error = True
    opened = []
    messages = []
    monkeypatch.setattr(
        app_module.permissions, "open_input_monitoring_pane", lambda: opened.append(True)
    )
    monkeypatch.setattr(
        app_module.QSystemTrayIcon, "showMessage", lambda *args: messages.append(args)
    )

    app = _build_app(qapp, fakes)

    assert opened == [True]
    assert messages
    app.shutdown()


def test_startup_prompts_screen_recording_before_input_monitoring(qapp, config, fakes, monkeypatch):
    monkeypatch.setattr(app_module.sys, "platform", "darwin")
    _capturer, hotkeys, _autostart = fakes
    hotkeys.raise_permission_error = True
    opened = []
    messages = []
    monkeypatch.setattr(
        app_module.permissions, "screen_capture_status", lambda: PermissionStatus.DENIED
    )
    monkeypatch.setattr(
        app_module.permissions, "open_screen_capture_pane", lambda: opened.append("screen")
    )
    monkeypatch.setattr(
        app_module.permissions, "open_input_monitoring_pane", lambda: opened.append("input")
    )
    monkeypatch.setattr(
        app_module.QSystemTrayIcon, "showMessage", lambda *args: messages.append(args)
    )

    app = _build_app(qapp, fakes)

    assert opened == ["screen"]
    assert hotkeys.bindings == {}
    body = " ".join(str(part) for message in messages for part in message)
    assert "Screen Recording" in body
    app.shutdown()


def test_startup_prompts_input_monitoring_after_screen_recording_is_granted(
    qapp, config, fakes, monkeypatch
):
    monkeypatch.setattr(app_module.sys, "platform", "darwin")
    _capturer, hotkeys, _autostart = fakes
    hotkeys.raise_permission_error = True
    status = PermissionStatus.DENIED
    opened = []
    monkeypatch.setattr(app_module.permissions, "screen_capture_status", lambda: status)
    monkeypatch.setattr(
        app_module.permissions, "open_screen_capture_pane", lambda: opened.append("screen")
    )
    monkeypatch.setattr(
        app_module.permissions, "open_input_monitoring_pane", lambda: opened.append("input")
    )
    monkeypatch.setattr(app_module.QSystemTrayIcon, "showMessage", lambda *args: None)

    app = _build_app(qapp, fakes)
    status = PermissionStatus.GRANTED
    app._retry_pending_permissions(Qt.ApplicationState.ApplicationActive)

    assert opened == ["screen", "input"]
    assert set(hotkeys.bindings) == {"<alt>+a", "<alt>+s"}
    app.shutdown()


def test_apply_hotkeys_skips_macos_pane_off_darwin(qapp, config, fakes, monkeypatch):
    # The Input Monitoring deep-link is an `x-apple-systempreferences:` URL
    # opened via the macOS `open` binary — on Linux that's a no-op (or spawns
    # the wrong handler), so the app must not call it. The user still gets the
    # notification; nothing tries to "fix" something Linux doesn't have.
    monkeypatch.setattr(app_module.sys, "platform", "linux")
    _capturer, hotkeys, _autostart = fakes
    hotkeys.raise_permission_error = True
    opened = []
    messages = []
    monkeypatch.setattr(
        app_module.permissions, "open_input_monitoring_pane", lambda: opened.append(True)
    )
    monkeypatch.setattr(
        app_module.QSystemTrayIcon, "showMessage", lambda *args: messages.append(args)
    )

    app = _build_app(qapp, fakes)

    assert opened == []  # macOS-only pane never opened on Linux
    assert messages  # but the user is still told something is wrong
    app.shutdown()


def test_apply_hotkeys_notifies_on_wayland(qapp, config, fakes, monkeypatch):
    # HotkeyUnavailable (e.g. Wayland blocks global key grabs) is unactionable:
    # show the reason so the user knows why their hotkey is dead, but never
    # spawn the macOS settings pane — there is no permission to grant.
    _capturer, hotkeys, _autostart = fakes
    hotkeys.raise_unavailable = "Wayland blocks global key grabs."
    opened = []
    messages = []
    monkeypatch.setattr(
        app_module.permissions, "open_input_monitoring_pane", lambda: opened.append(True)
    )
    monkeypatch.setattr(
        app_module.QSystemTrayIcon, "showMessage", lambda *args: messages.append(args)
    )

    app = _build_app(qapp, fakes)

    assert opened == []
    assert messages, "user must be told why hotkeys are silent"
    body = " ".join(str(part) for part in messages[0])
    assert "Wayland" in body
    app.shutdown()


def test_grab_returns_qimage_on_success(qapp, config, fakes):
    app = _build_app(qapp, fakes)
    image = app._grab(bl.Blocklist())
    assert image is not None
    assert (image.width(), image.height()) == (4, 3)
    app.shutdown()


def test_grab_returns_none_on_capture_failure(qapp, config, fakes):
    capturer, _hotkeys, _autostart = fakes
    app = _build_app(qapp, fakes)
    capturer.fail = True
    assert app._grab(bl.Blocklist()) is None  # error is reported via the tray, not raised
    app.shutdown()


def test_grab_takes_qimage_fast_path_when_nothing_blocked(qapp, config, fakes, monkeypatch):
    # No blocklist hit on screen: _grab must take the QImage straight from the
    # backend (capture_fullscreen_image) and never touch the CaptureResult bytes
    # round-trip — the copies that thrash swap under memory pressure.
    from PySide6.QtGui import QImage

    capturer, _hotkeys, _autostart = fakes
    sentinel = QImage(7, 5, QImage.Format.Format_RGBA8888)
    seen = []
    capturer.capture_fullscreen_image = lambda exclude=frozenset(): seen.append(exclude) or sentinel
    monkeypatch.setattr(
        capturer,
        "capture_fullscreen",
        lambda exclude_window_ids=frozenset(): pytest.fail("bytes round-trip used, not fast path"),
    )
    app = _build_app(qapp, fakes)
    assert app._grab(bl.Blocklist()) is sentinel
    assert seen == [frozenset()]
    app.shutdown()


def test_grab_fast_path_writes_completion_debug_log(qapp, config, fakes, monkeypatch, tmp_path):
    from PySide6.QtGui import QImage

    log = tmp_path / "debug.log"
    config.set_debug_mode(True)
    monkeypatch.setattr(paths, "debug_log_path", lambda: log)
    capturer, _hotkeys, _autostart = fakes
    sentinel = QImage(7, 5, QImage.Format.Format_RGBA8888)
    capturer.capture_fullscreen_image = lambda exclude=frozenset(): sentinel

    app = _build_app(qapp, fakes)
    app._current_debug_id = "capture-test"
    assert app._grab(bl.Blocklist()) is sentinel
    for handler in debug_log._debug_handlers(debug_log.get_logger()):
        handler.flush()

    text = log.read_text(encoding="utf-8")
    assert "op=capture-test" in text
    assert "backend=qimage_fast" in text
    app.shutdown()
    debug_log.configure(None)


def test_grab_falls_back_when_backend_has_no_fast_path(qapp, config, fakes):
    # A duck-typed capturer without capture_fullscreen_image (the shared fake)
    # must still work — _grab falls through to the CaptureResult route.
    capturer, _hotkeys, _autostart = fakes
    assert not hasattr(capturer, "capture_fullscreen_image")
    app = _build_app(qapp, fakes)
    image = app._grab(bl.Blocklist())
    assert image is not None
    assert (image.width(), image.height()) == (4, 3)  # the fake's bytes grab
    app.shutdown()


def test_grab_with_supplied_blocked_skips_window_enumeration(qapp, config, fakes):
    # When the caller hands _grab the on-screen blocklisted windows it already
    # resolved (the smart overlay), _grab must not enumerate the window list a
    # second time, even with a non-empty blocklist.
    capturer, _hotkeys, _autostart = fakes
    enumerations = []
    capturer.list_windows = lambda: enumerations.append(1) or []
    rules = bl.Blocklist((bl.BlockRule(bundle_id="com.example.blocked"),))
    app = _build_app(qapp, fakes)
    image = app._grab(rules, blocked=[])
    assert image is not None
    assert enumerations == []  # blocked supplied → no second enumeration
    app.shutdown()


def test_sync_autostart_follows_config(qapp, config, fakes):
    config.set_autostart(True)
    _capturer, _hotkeys, autostart = fakes
    app = _build_app(qapp, fakes)
    assert autostart.last is True
    app.shutdown()


def test_sync_autostart_swallows_oserror(qapp, config, fakes):
    _capturer, _hotkeys, autostart = fakes
    autostart.raise_oserror = True
    # Construction calls _sync_autostart; an OSError there must not crash startup.
    app = _build_app(qapp, fakes)
    app.shutdown()


def test_capture_fullscreen_opens_editor_over_the_screen(qapp, config, fakes, monkeypatch):
    # With auto-output off, a capture falls through to the editor, placed over
    # the captured area (the whole virtual desktop for a full-screen shot).
    config.set_auto_save_after_capture(False)
    config.set_auto_copy_after_capture(False)
    app = _build_app(qapp, fakes)
    opened = []
    monkeypatch.setattr(
        app,
        "_open_editor",
        lambda image, origin=None, region=None: opened.append((image, origin)),
    )
    app._capture_fullscreen()
    assert len(opened) == 1
    image, origin = opened[0]
    assert (image.width(), image.height()) == (4, 3)
    assert origin == qapp.primaryScreen().virtualGeometry()
    app.shutdown()


def test_capture_fullscreen_does_nothing_on_failure(qapp, config, fakes, monkeypatch):
    capturer, _hotkeys, _autostart = fakes
    capturer.fail = True
    app = _build_app(qapp, fakes)
    opened = []
    monkeypatch.setattr(
        app,
        "_open_editor",
        lambda image, origin=None, region=None: opened.append((image, origin)),
    )
    app._capture_fullscreen()
    assert opened == []
    app.shutdown()


def test_capture_window_image_delivers_capture(qapp, config, fakes, monkeypatch):
    from PySide6.QtCore import QRect

    app = _build_app(qapp, fakes)
    delivered = []
    monkeypatch.setattr(
        app,
        "_deliver_capture",
        lambda image, origin=None, region=None: delivered.append((image, origin)),
    )
    origin = QRect(10, 20, 4, 3)
    app._capture_window_image(42, origin)
    assert len(delivered) == 1
    image, got_origin = delivered[0]
    assert (image.width(), image.height()) == (4, 3)
    assert got_origin == origin
    app.shutdown()


def test_capture_window_image_delivers_non_adjustable_backdrop_context(
    qapp, config, fakes, monkeypatch
):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage

    app = _build_app(qapp, fakes)
    app._smart_screenshot = QImage(40, 30, QImage.Format.Format_ARGB32)
    app._smart_geometry = QRect(0, 0, 40, 30)
    delivered = []
    monkeypatch.setattr(
        app,
        "_deliver_capture",
        lambda image, origin=None, region=None: delivered.append((origin, region)),
    )

    origin = QRect(10, 20, 4, 3)
    app._capture_window_image(42, origin)

    assert len(delivered) == 1
    got_origin, region = delivered[0]
    assert got_origin == origin
    assert region is not None
    assert region.screenshot is app._smart_screenshot
    assert region.geometry == app._smart_geometry
    assert region.adjustable is False
    app.shutdown()


def test_capture_window_image_notifies_on_failure(qapp, config, fakes, monkeypatch):
    # A window that vanished between overlay and click must report "capture
    # failed" via the tray instead of crashing or opening an empty editor.
    from PySide6.QtCore import QRect

    from shotquill import i18n

    capturer, _hotkeys, _autostart = fakes
    app = _build_app(qapp, fakes)
    capturer.fail = True
    delivered = []
    notified = []
    monkeypatch.setattr(
        app, "_deliver_capture", lambda image, origin=None, region=None: delivered.append(image)
    )
    monkeypatch.setattr(app, "_notify", notified.append)
    app._capture_window_image(42, QRect(0, 0, 4, 3))
    assert delivered == []
    assert notified == [i18n.t("notify.capture_failed").format(error="no permission")]
    app.shutdown()


def test_window_preview_image_returns_none_on_failure(qapp, config, fakes):
    # The overlay's hover preview must degrade to the frozen screenshot (None)
    # when the un-occluded window grab fails, never raise into the worker.
    capturer, _hotkeys, _autostart = fakes
    app = _build_app(qapp, fakes)
    assert app._window_preview_image(42) is not None
    capturer.fail = True
    assert app._window_preview_image(42) is None
    app.shutdown()


# --- blocklist enforcement on the GUI path ----------------------------------


def _block(bundle_id=None, name=None):
    bl.save(bl.Blocklist((bl.BlockRule(bundle_id=bundle_id, name=name),)))


def test_grab_redacts_blocklisted_window(qapp, config, fakes, monkeypatch):
    # Fallback path: a backend that can't omit the window at capture time gets a
    # blocklisted window painted out of the full-screen grab — the same
    # protection the CLI/MCP get, now on the human hotkey path. (The fakes'
    # capture_fullscreen reports excluding nothing.)
    capturer, _hotkeys, _autostart = fakes
    monkeypatch.setattr(
        capturer,
        "list_windows",
        lambda: [
            WindowInfo(1, "1Password", "", Rect(0, 0, 2, 2), bundle_id="com.1password.1password")
        ],
    )
    _block(bundle_id="com.1password.1password")
    app = _build_app(qapp, fakes)
    image = app._grab(bl.load())
    assert image.pixelColor(0, 0).getRgb()[:3] == (0, 0, 0)  # window area blacked out
    assert image.pixelColor(3, 2).getRgb()[:3] == (255, 255, 255)  # the rest untouched
    app.shutdown()


def test_grab_excludes_blocklisted_window_without_painting(qapp, config, fakes, monkeypatch):
    # When the backend omits the window from the capture itself (macOS SCK), the
    # grab passes the blocked window's id to capture_fullscreen and paints
    # nothing — the window is simply absent, so the frame stays untouched.
    capturer, _hotkeys, _autostart = fakes
    monkeypatch.setattr(
        capturer,
        "list_windows",
        lambda: [
            WindowInfo(1, "1Password", "", Rect(0, 0, 2, 2), bundle_id="com.1password.1password")
        ],
    )
    seen = {}

    def excluding_capture(exclude_window_ids=frozenset()):
        seen["ids"] = exclude_window_ids
        return CaptureResult(
            width=4,
            height=3,
            scale=1.0,
            pixels=bytes([255] * 4 * 4 * 3),
            excluded_window_ids=frozenset(exclude_window_ids),
        )

    monkeypatch.setattr(capturer, "capture_fullscreen", excluding_capture)
    _block(bundle_id="com.1password.1password")
    app = _build_app(qapp, fakes)
    image = app._grab(bl.load())
    assert seen["ids"] == frozenset({1})  # the blocked window's id was handed to the backend
    assert image.pixelColor(0, 0).getRgb()[:3] == (255, 255, 255)  # excluded, not painted over
    app.shutdown()


def test_grab_unchanged_without_blocklist(qapp, config, fakes, monkeypatch):
    capturer, _hotkeys, _autostart = fakes
    monkeypatch.setattr(
        capturer, "list_windows", lambda: [WindowInfo(1, "1Password", "", Rect(0, 0, 2, 2))]
    )
    app = _build_app(qapp, fakes)  # no blocklist file → empty
    image = app._grab(bl.Blocklist())
    assert image.pixelColor(0, 0).getRgb()[:3] == (255, 255, 255)  # nothing painted
    app.shutdown()


def test_capture_window_image_refuses_blocklisted_window(qapp, config, fakes, monkeypatch):
    from PySide6.QtCore import QRect

    app = _build_app(qapp, fakes)
    app._blocked_windows = {42: WindowInfo(42, "1Password", "", Rect(0, 0, 2, 2))}
    delivered, notified = [], []
    monkeypatch.setattr(app, "_deliver_capture", lambda *a, **k: delivered.append(a))
    monkeypatch.setattr(app, "_notify", notified.append)
    app._capture_window_image(42, QRect(0, 0, 2, 2))
    assert delivered == []  # never captured
    assert notified and "1Password" in notified[0]
    app.shutdown()


def test_capture_window_image_redacts_blocked_overlap_on_framebuffer_backend(
    qapp, config, fakes, monkeypatch
):
    # No-compositor X11: grabbing the allowed target also reads a blocklisted
    # window stacked over it. The overlapping pixels must be painted out before
    # the capture is delivered.
    from PySide6.QtCore import QRect

    capturer, _hotkeys, _autostart = fakes
    app = _build_app(qapp, fakes)
    monkeypatch.setattr(capturer, "window_capture_includes_overlaps", lambda: True, raising=False)
    app._blocked_windows = {
        99: WindowInfo(99, "1Password", "", Rect(10, 20, 4, 3), bundle_id="com.1password.1password")
    }
    delivered = []
    monkeypatch.setattr(
        app, "_deliver_capture", lambda image, origin=None, region=None: delivered.append(image)
    )
    app._capture_window_image(42, QRect(10, 20, 4, 3))  # allowed target, blocked overlap
    assert len(delivered) == 1
    assert delivered[0].pixelColor(0, 0).red() == 0  # redacted to black
    app.shutdown()


def test_capture_window_image_keeps_capture_on_surface_backend(qapp, config, fakes, monkeypatch):
    # Surface-accurate backend: no overlap can leak, so nothing is redacted even
    # with a blocklisted window nominally overlapping the target's bounds.
    from PySide6.QtCore import QRect

    capturer, _hotkeys, _autostart = fakes
    app = _build_app(qapp, fakes)
    monkeypatch.setattr(capturer, "window_capture_includes_overlaps", lambda: False, raising=False)
    app._blocked_windows = {
        99: WindowInfo(99, "1Password", "", Rect(10, 20, 4, 3), bundle_id="com.1password.1password")
    }
    delivered = []
    monkeypatch.setattr(
        app, "_deliver_capture", lambda image, origin=None, region=None: delivered.append(image)
    )
    app._capture_window_image(42, QRect(10, 20, 4, 3))
    assert len(delivered) == 1
    assert delivered[0].pixelColor(0, 0).red() == 255  # untouched
    app.shutdown()


def test_window_preview_skips_blocklisted_window(qapp, config, fakes):
    app = _build_app(qapp, fakes)
    app._blocked_windows = {7: WindowInfo(7, "1Password", "", Rect(0, 0, 2, 2))}
    assert app._window_preview_image(7) is None  # blocked → no preview pixels
    assert app._window_preview_image(99) is not None  # others preview normally
    app.shutdown()


def _corrupt_blocklist():
    from shotquill import paths

    paths.blocklist_path().write_text("{ not valid json", encoding="utf-8")


def test_load_blocklist_returns_none_and_notifies_on_corrupt(qapp, config, fakes, monkeypatch):
    # A present-but-corrupt list can't tell us what to protect, so the load
    # returns None (caller aborts) and warns the user — the GUI fails closed
    # like headless.
    _corrupt_blocklist()
    app = _build_app(qapp, fakes)
    notified = []
    monkeypatch.setattr(app, "_notify", notified.append)
    assert app._load_blocklist_or_abort() is None
    assert notified and "blocklist" in notified[0].casefold()
    app.shutdown()


def test_capture_fullscreen_fails_closed_on_corrupt_blocklist(qapp, config, fakes, monkeypatch):
    # The capture bails before grabbing any pixels — never hands a frame to the
    # editor/clipboard when it cannot honour the user's blocklist.
    _corrupt_blocklist()
    app = _build_app(qapp, fakes)
    grabbed, delivered, notified = [], [], []
    monkeypatch.setattr(app, "_grab", lambda *a: grabbed.append(True))
    monkeypatch.setattr(app, "_deliver_capture", lambda *a, **k: delivered.append(a))
    monkeypatch.setattr(app, "_notify", notified.append)
    app._capture_fullscreen()
    assert grabbed == [] and delivered == []  # aborted before grabbing
    assert notified  # user told why
    app.shutdown()


def test_smart_capture_fails_closed_on_corrupt_blocklist(qapp, config, fakes, monkeypatch):
    _corrupt_blocklist()
    app = _build_app(qapp, fakes)
    grabbed, notified = [], []
    monkeypatch.setattr(app, "_grab", lambda *a: grabbed.append(True))
    monkeypatch.setattr(app, "_notify", notified.append)
    app._capture_smart()
    assert grabbed == []  # no overlay built, no pixels grabbed
    assert notified
    app.shutdown()


def test_smart_capture_survives_list_windows_unsupported(qapp, config, fakes, monkeypatch):
    # The Linux QtGrabCapturer raises CapabilityUnsupported from list_windows()
    # because X11 window enumeration isn't implemented on that backend. The
    # smart-capture flow has to keep working — the overlay just loses the
    # per-window click target and degrades to region / full-screen modes.
    from shotquill.headless import CapabilityUnsupported

    capturer, _hotkeys, _autostart = fakes

    def _raise(*_args, **_kwargs):
        raise CapabilityUnsupported(
            "list_windows", "window enumeration is not implemented on this backend yet"
        )

    monkeypatch.setattr(capturer, "list_windows", _raise)
    app = _build_app(qapp, fakes)
    # The call must not raise and must leave the blocked-window set empty —
    # nothing was enumerable, so nothing can be marked blocked; the overlay
    # just degrades to region / full-screen modes.
    app._capture_smart()
    assert app._blocked_windows == {}
    app.shutdown()


def test_wayland_smart_capture_uses_portal_picker(qapp, config, fakes, monkeypatch):
    capturer, _hotkeys, _autostart = fakes
    monkeypatch.setattr(qapp, "platformName", lambda: "wayland")
    monkeypatch.setattr(
        capturer,
        "list_windows",
        lambda: (_ for _ in ()).throw(AssertionError("should not enumerate windows")),
    )
    picked = []

    def _interactive():
        picked.append(True)
        return CaptureResult(
            width=8,
            height=6,
            scale=2.0,
            pixels=bytes([255] * 8 * 6 * 4),
            origin_x=100,
            origin_y=50,
        )

    monkeypatch.setattr(capturer, "capture_interactive", _interactive)
    app = _build_app(qapp, fakes)
    delivered = []

    def _deliver(image, origin=None, **_kwargs):
        delivered.append((image, origin))

    monkeypatch.setattr(app, "_deliver_capture", _deliver)

    app._capture_smart()

    assert picked == [True]
    assert delivered and delivered[0][0].size().width() == 8
    assert delivered[0][1] is None
    assert not any(isinstance(window, app_module.SmartOverlay) for window in app._windows)
    app.shutdown()


def test_wayland_smart_capture_refuses_blocklist_before_picker(qapp, config, fakes, monkeypatch):
    from shotquill import blocklist as bl

    bl.save(bl.Blocklist((bl.BlockRule(name="password"),)))
    capturer, _hotkeys, _autostart = fakes
    monkeypatch.setattr(qapp, "platformName", lambda: "wayland")
    picked, notified = [], []
    monkeypatch.setattr(capturer, "capture_interactive", lambda: picked.append(True))
    app = _build_app(qapp, fakes)
    monkeypatch.setattr(app, "_notify", notified.append)

    app._capture_smart()

    assert picked == []
    assert notified and "blocklist" in notified[-1].lower()
    app.shutdown()


def test_wayland_smart_capture_refuses_allowlist_before_picker(qapp, config, fakes, monkeypatch):
    from shotquill import allowlist as al
    from shotquill import blocklist as bl

    al.save(al.Allowlist(enabled=True, rules=(bl.BlockRule(name="terminal"),)))
    capturer, _hotkeys, _autostart = fakes
    monkeypatch.setattr(qapp, "platformName", lambda: "wayland")
    picked, notified = [], []
    monkeypatch.setattr(capturer, "capture_interactive", lambda: picked.append(True))
    app = _build_app(qapp, fakes)
    monkeypatch.setattr(app, "_notify", notified.append)

    app._capture_smart()

    assert picked == []
    assert notified and "allowlist" in notified[-1].lower()
    app.shutdown()


def test_fullscreen_capture_refuses_blocklist_when_windows_unavailable(
    qapp, config, fakes, monkeypatch
):
    from shotquill import blocklist as bl
    from shotquill.headless import CapabilityUnsupported

    bl.save(bl.Blocklist((bl.BlockRule(name="password"),)))
    capturer, _hotkeys, _autostart = fakes

    def _raise(*_args, **_kwargs):
        raise CapabilityUnsupported("list_windows", "Wayland does not allow window enumeration")

    monkeypatch.setattr(capturer, "list_windows", _raise)
    grabbed, delivered, notified = [], [], []
    monkeypatch.setattr(capturer, "capture_fullscreen", lambda *a, **k: grabbed.append(True))
    app = _build_app(qapp, fakes)
    monkeypatch.setattr(app, "_deliver_capture", lambda *a, **k: delivered.append(a))
    monkeypatch.setattr(app, "_notify", notified.append)
    app._capture_fullscreen()
    assert grabbed == [] and delivered == []
    assert notified and "can't inspect windows" in notified[-1]
    app.shutdown()


def test_smart_capture_refuses_blocklist_when_windows_unavailable(qapp, config, fakes, monkeypatch):
    from shotquill import blocklist as bl
    from shotquill.headless import CapabilityUnsupported

    bl.save(bl.Blocklist((bl.BlockRule(name="password"),)))
    capturer, _hotkeys, _autostart = fakes

    def _raise(*_args, **_kwargs):
        raise CapabilityUnsupported("list_windows", "Wayland does not allow window enumeration")

    monkeypatch.setattr(capturer, "list_windows", _raise)
    app = _build_app(qapp, fakes)
    grabbed, notified = [], []
    monkeypatch.setattr(app, "_grab", lambda *a, **k: grabbed.append(True))
    monkeypatch.setattr(app, "_notify", notified.append)
    app._capture_smart()
    assert grabbed == []
    assert notified and "can't inspect windows" in notified[-1]
    app.shutdown()


def test_smart_capture_refuses_allowlist_when_windows_unavailable(qapp, config, fakes, monkeypatch):
    from shotquill import allowlist as al
    from shotquill import blocklist as bl
    from shotquill.headless import CapabilityUnsupported

    al.save(al.Allowlist(enabled=True, rules=(bl.BlockRule(name="terminal"),)))
    capturer, _hotkeys, _autostart = fakes

    def _raise(*_args, **_kwargs):
        raise CapabilityUnsupported("list_windows", "Wayland does not allow window enumeration")

    monkeypatch.setattr(capturer, "list_windows", _raise)
    app = _build_app(qapp, fakes)
    grabbed, notified = [], []
    monkeypatch.setattr(app, "_grab", lambda *a, **k: grabbed.append(True))
    monkeypatch.setattr(app, "_notify", notified.append)
    app._capture_smart()
    assert grabbed == []
    assert notified and "can't inspect windows" in notified[-1]
    app.shutdown()


# --- allowlist enforcement on the GUI path ----------------------------------


def _enable_allowlist(*rules):
    from shotquill import allowlist as al

    al.save(al.Allowlist(enabled=True, rules=rules))


def test_capture_fullscreen_refused_when_allowlist_enabled(qapp, config, fakes, monkeypatch):
    # A whole-screen grab cannot honour "only these apps", so the allowlist
    # refuses it on the human hotkey path too — no pixels reach the editor.
    from shotquill import blocklist as bl

    _enable_allowlist(bl.BlockRule(name="terminal"))
    app = _build_app(qapp, fakes)
    grabbed, opened, notified = [], [], []
    monkeypatch.setattr(app, "_grab", lambda *a: grabbed.append(True))
    monkeypatch.setattr(app, "_open_editor", lambda *a, **k: opened.append(a))
    monkeypatch.setattr(app, "_notify", notified.append)
    app._capture_fullscreen()
    assert grabbed == [] and opened == []  # refused before grabbing
    assert notified  # user told why
    app.shutdown()


def test_smart_region_refused_when_allowlist_enabled(qapp, config, fakes, monkeypatch):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage

    from shotquill import allowlist as al
    from shotquill import blocklist as bl

    app = _build_app(qapp, fakes)
    app._allowlist = al.Allowlist(enabled=True, rules=(bl.BlockRule(name="terminal"),))
    delivered, notified = [], []
    monkeypatch.setattr(app, "_deliver_capture", lambda *a, **k: delivered.append(a))
    monkeypatch.setattr(app, "_notify", notified.append)
    app._smart_screenshot, app._smart_geometry = QImage(), QRect()
    app._smart_region_selected(QImage(), QRect(0, 0, 2, 2))
    assert delivered == []
    assert notified
    app.shutdown()


def test_smart_fullscreen_refused_when_allowlist_enabled(qapp, config, fakes, monkeypatch):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage

    from shotquill import allowlist as al
    from shotquill import blocklist as bl

    app = _build_app(qapp, fakes)
    app._allowlist = al.Allowlist(enabled=True, rules=(bl.BlockRule(name="terminal"),))
    delivered, notified = [], []
    monkeypatch.setattr(app, "_deliver_capture", lambda *a, **k: delivered.append(a))
    monkeypatch.setattr(app, "_notify", notified.append)
    app._smart_screenshot, app._smart_geometry = QImage(), QRect()
    app._smart_fullscreen_selected()
    assert delivered == []
    assert notified
    app.shutdown()


def test_smart_region_delivers_when_allowlist_disabled(qapp, config, fakes, monkeypatch):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage

    from shotquill import allowlist as al

    app = _build_app(qapp, fakes)
    app._allowlist = al.Allowlist()  # disabled
    delivered = []
    monkeypatch.setattr(app, "_deliver_capture", lambda *a, **k: delivered.append(a))
    app._smart_screenshot, app._smart_geometry = QImage(), QRect()
    app._smart_region_selected(QImage(), QRect(0, 0, 2, 2))
    assert len(delivered) == 1
    app.shutdown()


def test_smart_region_marks_context_not_adjustable_when_adjust_disabled(
    qapp, config, fakes, monkeypatch
):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage

    from shotquill import allowlist as al

    config.set_region_adjust(False)
    app = _build_app(qapp, fakes)
    app._allowlist = al.Allowlist()  # disabled
    delivered = []
    monkeypatch.setattr(
        app,
        "_deliver_capture",
        lambda image, origin=None, region=None: delivered.append((origin, region)),
    )
    app._smart_screenshot = QImage(4, 3, QImage.Format.Format_ARGB32)
    app._smart_geometry = QRect(0, 0, 4, 3)

    app._smart_region_selected(QImage(2, 2, QImage.Format.Format_ARGB32), QRect(0, 0, 2, 2))

    assert len(delivered) == 1
    origin, region = delivered[0]
    assert origin == QRect(0, 0, 2, 2)
    assert region is not None
    assert region.geometry == QRect(0, 0, 4, 3)
    assert region.adjustable is False
    app.shutdown()


def test_capture_window_image_refuses_not_allowed_window(qapp, config, fakes, monkeypatch):
    from PySide6.QtCore import QRect

    app = _build_app(qapp, fakes)
    app._not_allowed_windows = {42: WindowInfo(42, "Safari", "", Rect(0, 0, 2, 2))}
    delivered, notified = [], []
    monkeypatch.setattr(app, "_deliver_capture", lambda *a, **k: delivered.append(a))
    monkeypatch.setattr(app, "_notify", notified.append)
    app._capture_window_image(42, QRect(0, 0, 2, 2))
    assert delivered == []
    assert notified  # told it isn't on the allowlist
    app.shutdown()


def test_window_preview_skips_not_allowed_window(qapp, config, fakes):
    app = _build_app(qapp, fakes)
    app._not_allowed_windows = {7: WindowInfo(7, "Safari", "", Rect(0, 0, 2, 2))}
    assert app._window_preview_image(7) is None  # not allowed → no preview pixels
    assert app._window_preview_image(99) is not None  # an allowed window previews
    app.shutdown()


def test_smart_capture_marks_not_allowed_windows(qapp, config, fakes, monkeypatch):
    # With the allowlist on, on-screen windows that aren't allowed are marked so
    # the click/preview paths refuse them — the allowed one is left capturable.
    from shotquill import blocklist as bl

    capturer, _hotkeys, _autostart = fakes
    allowed = WindowInfo(1, "Terminal", "zsh", Rect(0, 0, 2, 2))
    other = WindowInfo(2, "Safari", "GitHub", Rect(0, 0, 2, 2))
    monkeypatch.setattr(capturer, "list_windows", lambda: [allowed, other])
    _enable_allowlist(bl.BlockRule(name="terminal"))
    app = _build_app(qapp, fakes)
    app._capture_smart()
    assert set(app._not_allowed_windows) == {2}  # only the non-allowed window
    assert app._blocked_windows == {}
    app.shutdown()


def test_load_allowlist_returns_none_and_notifies_on_corrupt(qapp, config, fakes, monkeypatch):
    from shotquill import paths

    paths.allowlist_path().write_text("{ not valid json", encoding="utf-8")
    app = _build_app(qapp, fakes)
    notified = []
    monkeypatch.setattr(app, "_notify", notified.append)
    assert app._load_allowlist_or_abort() is None
    assert notified and "allowlist" in notified[0].casefold()
    app.shutdown()


def test_capture_fullscreen_fails_closed_on_corrupt_allowlist(qapp, config, fakes, monkeypatch):
    from shotquill import paths

    paths.allowlist_path().write_text("{ not valid json", encoding="utf-8")
    app = _build_app(qapp, fakes)
    grabbed, notified = [], []
    monkeypatch.setattr(app, "_grab", lambda *a: grabbed.append(True))
    monkeypatch.setattr(app, "_notify", notified.append)
    app._capture_fullscreen()
    assert grabbed == []  # aborted before grabbing
    assert notified
    app.shutdown()


def test_capture_window_image_redacts_not_allowed_overlap_on_framebuffer_backend(
    qapp, config, fakes, monkeypatch
):
    # No-compositor X11: grabbing an allowlisted target also reads a non-allowed
    # window stacked over it — those pixels must be painted out before delivery,
    # the same protection blocklisted overlaps get.
    from PySide6.QtCore import QRect

    capturer, _hotkeys, _autostart = fakes
    app = _build_app(qapp, fakes)
    monkeypatch.setattr(capturer, "window_capture_includes_overlaps", lambda: True, raising=False)
    # 42 (the allowed target) is captured; 99 is not on the allowlist and overlaps.
    app._not_allowed_windows = {99: WindowInfo(99, "Safari", "", Rect(10, 20, 4, 3))}
    delivered = []
    monkeypatch.setattr(
        app, "_deliver_capture", lambda image, origin=None, region=None: delivered.append(image)
    )
    app._capture_window_image(42, QRect(10, 20, 4, 3))
    assert len(delivered) == 1
    assert delivered[0].pixelColor(0, 0).red() == 0  # the non-allowed overlap is redacted
    app.shutdown()


def test_smart_capture_marks_blocklisted_windows(qapp, config, fakes, monkeypatch):
    capturer, _hotkeys, _autostart = fakes
    monkeypatch.setattr(
        capturer,
        "list_windows",
        lambda: [
            WindowInfo(1, "1Password", "", Rect(0, 0, 2, 2), bundle_id="com.1password.1password"),
            WindowInfo(2, "Safari", "", Rect(0, 0, 2, 2), bundle_id="com.apple.safari"),
        ],
    )
    _block(name="1password")
    app = _build_app(qapp, fakes)
    app._capture_smart()
    assert set(app._blocked_windows) == {1}  # only the blocked window is marked
    app.shutdown()


def test_auto_save_failure_notifies_and_falls_back_to_editor(qapp, config, fakes, monkeypatch):
    # A failed auto-save must report "save failed" (not "capture failed") and
    # return False so _deliver_capture opens the editor — otherwise the shot
    # would be lost entirely, with only a notification to show for it.
    from PySide6.QtGui import QImage

    from shotquill import i18n
    from shotquill.output import saver

    config.set_auto_save_after_capture(True)
    config.set_auto_copy_after_capture(False)
    app = _build_app(qapp, fakes)

    err = OSError("disk full")

    def _failing_save(image, directory, image_format="png"):
        raise err

    monkeypatch.setattr(saver, "save_qimage", _failing_save)
    notified = []
    monkeypatch.setattr(app, "_notify", notified.append)

    image = QImage(4, 3, QImage.Format.Format_ARGB32)
    assert app._auto_output(image) is False  # not handled: editor fallback
    assert notified == [i18n.t("notify.save_failed").format(error=err)]
    app.shutdown()


def test_deliver_capture_opens_editor_when_auto_save_fails(qapp, config, fakes, monkeypatch):
    from PySide6.QtGui import QImage

    from shotquill.output import saver

    config.set_auto_save_after_capture(True)
    config.set_auto_copy_after_capture(False)
    app = _build_app(qapp, fakes)

    def _failing_save(image, directory, image_format="png"):
        raise OSError("disk full")

    monkeypatch.setattr(saver, "save_qimage", _failing_save)
    monkeypatch.setattr(app, "_notify", lambda message: None)
    opened = []
    monkeypatch.setattr(
        app, "_open_editor", lambda image, origin=None, region=None: opened.append(image)
    )

    app._deliver_capture(QImage(4, 3, QImage.Format.Format_ARGB32))
    assert len(opened) == 1
    app.shutdown()


def test_region_capture_hands_the_editor_a_region_context(qapp, config, fakes, monkeypatch):
    # A region selection must carry the full screenshot along so the editor
    # can keep the crop arrow-key adjustable.
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage

    config.set_auto_save_after_capture(False)
    config.set_auto_copy_after_capture(False)
    app = _build_app(qapp, fakes)
    opened = []
    monkeypatch.setattr(
        app,
        "_open_editor",
        lambda image, origin=None, region=None: opened.append((origin, region)),
    )

    app._capture_smart()
    overlay = next(w for w in app._windows if isinstance(w, app_module.SmartOverlay))
    crop = QImage(2, 2, QImage.Format.Format_ARGB32)
    overlay.region_selected.emit(crop, QRect(1, 1, 2, 2))
    overlay.close()

    assert len(opened) == 1
    origin, region = opened[0]
    assert origin == QRect(1, 1, 2, 2)
    assert region is not None
    assert (region.screenshot.width(), region.screenshot.height()) == (4, 3)
    assert region.geometry == qapp.primaryScreen().virtualGeometry()
    assert region.adjustable is True
    app.shutdown()


def test_open_editor_honours_region_adjust_setting(qapp, config, fakes):
    # Turning the Settings toggle off must keep the spotlight backdrop context
    # but freeze the crop (no arrow-key adjustment).
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage

    from shotquill.ui.editor import RegionContext
    from shotquill.ui.editor_core import EditorCoreMixin  # matches either editor shell

    app = _build_app(qapp, fakes)
    image = QImage(4, 3, QImage.Format.Format_ARGB32)
    region = RegionContext(image, QRect(0, 0, 4, 3))

    app._open_editor(image, QRect(0, 0, 4, 3), region)
    adjustable = [w for w in app._windows if isinstance(w, EditorCoreMixin)][-1]
    assert adjustable._region is not None
    assert adjustable.crop_adjustable() is True

    config.set_region_adjust(False)
    app._open_editor(image, QRect(0, 0, 4, 3), region)
    frozen = [w for w in app._windows if isinstance(w, EditorCoreMixin)][-1]
    assert frozen._region is not None
    assert frozen._region.adjustable is False
    assert frozen.crop_adjustable() is False

    adjustable.close()
    frozen.close()
    app.shutdown()


def test_track_and_forget_window_bookkeeping(qapp, config, fakes):
    from PySide6.QtWidgets import QWidget

    app = _build_app(qapp, fakes)
    widget = QWidget()
    app._track(widget)
    assert widget in app._windows
    app._forget(widget)
    assert widget not in app._windows
    app.shutdown()


def test_shutdown_stops_hotkeys(qapp, config, fakes):
    _capturer, hotkeys, _autostart = fakes
    app = _build_app(qapp, fakes)
    before = hotkeys.stopped
    app.shutdown()
    assert hotkeys.stopped > before


def test_capturer_gets_cursor_preference_from_config(qapp, config, fakes):
    config.set_include_cursor(True)
    capturer, _hotkeys, _autostart = fakes
    app = _build_app(qapp, fakes)
    assert capturer.include_cursor is True
    app.shutdown()


class _FakeSettingsDialog(QDialog):
    """Stands in for SettingsDialog: a plain QDialog that ignores the config."""

    uninstall_requested = Signal()

    def __init__(self, cfg):
        super().__init__()


class _FakeSignal:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args):
        for callback in tuple(self._callbacks):
            callback(*args)


class _FakeUninstallProcess:
    ProcessError = QProcess.ProcessError
    ExitStatus = QProcess.ExitStatus
    instances = []

    def __init__(self, parent):
        self.parent = parent
        self.finished = _FakeSignal()
        self.errorOccurred = _FakeSignal()
        self.program = None
        self.arguments = None
        self.started = False
        self.stderr = b""
        self.error_text = "could not start"
        self.deleted = False
        self.terminated = False
        self.killed = False
        self.instances.append(self)

    def setProgram(self, program):
        self.program = program

    def setArguments(self, arguments):
        self.arguments = arguments

    def start(self):
        self.started = True

    def readAllStandardError(self):
        return self.stderr

    def errorString(self):
        return self.error_text

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def deleteLater(self):
        self.deleted = True


def _bound_direct_uninstall_plan(*, app_path=True):
    from shotquill import uninstall

    digest = "a" * 64
    app_generation = f"1:2:{digest}" if app_path else "missing"
    return uninstall.UninstallPlan(
        channel=uninstall.InstallChannel.PKG,
        can_execute=True,
        remove_paths=(Path(uninstall.APP_PATH),) if app_path else (),
        forget_receipts=(uninstall.APP_RECEIPT,),
        brew_command=None,
        helper_path=Path(uninstall.UNINSTALL_HELPER_PATH),
        warnings=(),
        generation=uninstall.InstallGeneration(
            app=app_generation,
            helper=f"3:4:{'b' * 64}",
            shotquill="preserve",
            squill="preserve",
            launch_agent="missing",
        ),
    )


def test_open_settings_connects_uninstall_request(qapp, config, fakes, monkeypatch):
    app = _build_app(qapp, fakes)
    monkeypatch.setattr(app_module, "SettingsDialog", _FakeSettingsDialog)
    requested = []
    monkeypatch.setattr(app, "_request_uninstall", lambda: requested.append(True))

    app._open_settings()
    app._settings_dialog.uninstall_requested.emit()

    assert requested == [True]
    app.shutdown()


def test_uninstall_channel_probe_does_not_block_the_gui_thread(
    qapp, qtbot, config, fakes, monkeypatch
):
    import threading

    from shotquill import uninstall

    app = _build_app(qapp, fakes)
    plan = _bound_direct_uninstall_plan()
    started = threading.Event()
    release = threading.Event()

    def inspect():
        started.set()
        assert release.wait(2)
        return plan

    monkeypatch.setattr(uninstall, "prepare_uninstall_plan", inspect)
    presented = []
    monkeypatch.setattr(app, "_present_uninstall_plan", presented.append)

    app._request_uninstall()

    assert started.wait(1)
    assert app._uninstall_probe_running is True
    assert presented == []
    release.set()
    qtbot.waitUntil(lambda: presented == [plan])
    assert app._uninstall_probe_running is False
    app.shutdown()


def test_cancelled_uninstall_probe_ignores_its_eventual_result(
    qapp, qtbot, config, fakes, monkeypatch
):
    import threading

    from shotquill import uninstall

    app = _build_app(qapp, fakes)
    started = threading.Event()
    release = threading.Event()
    completed = threading.Event()

    def inspect():
        started.set()
        assert release.wait(2)
        completed.set()
        return object()

    monkeypatch.setattr(uninstall, "prepare_uninstall_plan", inspect)
    presented = []
    monkeypatch.setattr(app, "_present_uninstall_plan", presented.append)

    app._request_uninstall()
    assert started.wait(1)
    app._cancel_uninstall_probe()
    assert app._uninstall_probe_running is False
    release.set()

    assert completed.wait(1)
    qtbot.wait(20)
    assert presented == []
    app.shutdown()


def test_direct_pkg_uninstall_confirmation_launches_helper_and_quits(
    qapp, config, fakes, monkeypatch
):
    app = _build_app(qapp, fakes)
    autostart_before = fakes[2].last
    plan = _bound_direct_uninstall_plan()
    monkeypatch.setattr(app, "_confirm_uninstall", lambda _preview: True)
    _FakeUninstallProcess.instances.clear()
    monkeypatch.setattr(app_module, "QProcess", _FakeUninstallProcess)
    quit_requested = []
    monkeypatch.setattr(app._app, "quit", lambda: quit_requested.append(True))

    app._present_uninstall_plan(plan)

    process = _FakeUninstallProcess.instances[0]
    assert process.program == "/usr/bin/env"
    assert process.arguments[:1] == ["-i"]
    assert "--launch-gui-coordinator" in process.arguments
    assert process.started is True
    assert app._quit_action.isEnabled() is False
    assert all(action.isEnabled() is False for action in app._capture_actions)
    launch_timer = app._uninstall_launch_timer
    assert launch_timer.isActive() is True
    assert quit_requested == []

    app._request_quit()
    assert quit_requested == []

    process.finished.emit(0, QProcess.ExitStatus.NormalExit)

    assert quit_requested == [True]
    assert app._quit_action.isEnabled() is False
    assert all(action.isEnabled() is False for action in app._capture_actions)
    assert launch_timer.isActive() is False
    assert process.deleted is True
    assert fakes[2].last is autostart_before
    app.shutdown()


def test_cancelled_pkg_uninstall_keeps_the_app_running(qapp, config, fakes, monkeypatch):
    app = _build_app(qapp, fakes)
    autostart_before = fakes[2].last
    plan = _bound_direct_uninstall_plan()
    monkeypatch.setattr(app, "_confirm_uninstall", lambda _preview: False)
    quit_requested = []
    monkeypatch.setattr(app._app, "quit", lambda: quit_requested.append(True))

    app._present_uninstall_plan(plan)

    assert app._uninstall_process is None
    assert quit_requested == []
    assert fakes[2].last is autostart_before
    app.shutdown()


def test_failed_pkg_coordinator_start_keeps_app_and_autostart(qapp, config, fakes, monkeypatch):
    app = _build_app(qapp, fakes)
    autostart_before = fakes[2].last
    plan = _bound_direct_uninstall_plan()
    monkeypatch.setattr(app, "_confirm_uninstall", lambda _preview: True)
    _FakeUninstallProcess.instances.clear()
    monkeypatch.setattr(app_module, "QProcess", _FakeUninstallProcess)
    errors = []
    monkeypatch.setattr(
        app_module.QMessageBox,
        "critical",
        lambda *args, **kwargs: errors.append((args, kwargs)),
    )
    quit_requested = []
    monkeypatch.setattr(app._app, "quit", lambda: quit_requested.append(True))

    app._present_uninstall_plan(plan)
    process = _FakeUninstallProcess.instances[0]
    process.stderr = b"shotquill-uninstall: coordinator failed to start\n"
    process.finished.emit(1, QProcess.ExitStatus.NormalExit)

    assert errors
    assert quit_requested == []
    assert app._quit_action.isEnabled() is True
    assert all(action.isEnabled() is True for action in app._capture_actions)
    assert fakes[2].last is autostart_before
    app.shutdown()


def test_timed_out_pkg_coordinator_is_terminated_and_restores_interaction(
    qapp, config, fakes, monkeypatch
):
    app = _build_app(qapp, fakes)
    plan = _bound_direct_uninstall_plan()
    monkeypatch.setattr(app, "_confirm_uninstall", lambda _preview: True)
    _FakeUninstallProcess.instances.clear()
    monkeypatch.setattr(app_module, "QProcess", _FakeUninstallProcess)
    errors = []
    monkeypatch.setattr(
        app_module.QMessageBox,
        "critical",
        lambda *args, **kwargs: errors.append((args, kwargs)),
    )
    quit_requested = []
    monkeypatch.setattr(app._app, "quit", lambda: quit_requested.append(True))

    app._present_uninstall_plan(plan)
    process = _FakeUninstallProcess.instances[0]
    app._uninstall_launch_timed_out()

    assert process.terminated is True
    assert app._uninstall_process is process
    assert app._quit_action.isEnabled() is False
    kill_timer = app._uninstall_kill_timer
    assert kill_timer.isActive() is True

    process.finished.emit(15, QProcess.ExitStatus.CrashExit)

    assert errors
    assert "timed out" in errors[0][0][2]
    assert quit_requested == []
    assert app._uninstall_process is None
    assert app._quit_action.isEnabled() is True
    assert all(action.isEnabled() is True for action in app._capture_actions)
    assert kill_timer.isActive() is False
    assert process.deleted is True
    app.shutdown()


def test_timed_out_pkg_coordinator_is_killed_if_term_does_not_finish(
    qapp, config, fakes, monkeypatch
):
    app = _build_app(qapp, fakes)
    plan = _bound_direct_uninstall_plan()
    monkeypatch.setattr(app, "_confirm_uninstall", lambda _preview: True)
    _FakeUninstallProcess.instances.clear()
    monkeypatch.setattr(app_module, "QProcess", _FakeUninstallProcess)
    errors = []
    monkeypatch.setattr(
        app_module.QMessageBox,
        "critical",
        lambda *args, **kwargs: errors.append((args, kwargs)),
    )

    app._present_uninstall_plan(plan)
    process = _FakeUninstallProcess.instances[0]
    app._uninstall_launch_timed_out()
    app._kill_timed_out_uninstall_launcher()

    assert process.terminated is True
    assert process.killed is True
    assert errors
    assert app._uninstall_process is None
    assert app._quit_action.isEnabled() is True
    assert all(action.isEnabled() is True for action in app._capture_actions)
    assert process.deleted is False
    process.finished.emit(15, QProcess.ExitStatus.CrashExit)
    assert process.deleted is True
    app.shutdown()


def test_unsafe_homebrew_plan_never_shows_uninstall_command(qapp, config, fakes, monkeypatch):
    from shotquill import uninstall

    app = _build_app(qapp, fakes)
    plan = uninstall.UninstallPlan(
        channel=uninstall.InstallChannel.HOMEBREW,
        can_execute=False,
        remove_paths=(),
        forget_receipts=(),
        brew_command=None,
        helper_path=None,
        warnings=("Refusing an unrelated app.",),
    )
    warnings = []
    instructions = []
    monkeypatch.setattr(
        app_module.QMessageBox,
        "warning",
        lambda *args, **kwargs: warnings.append((args, kwargs)),
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "information",
        lambda *args, **kwargs: instructions.append((args, kwargs)),
    )

    app._present_uninstall_plan(plan)

    assert warnings
    assert instructions == []
    app.shutdown()


def test_open_settings_syncs_capturer_cursor_preference(qapp, config, fakes, monkeypatch):
    capturer, _hotkeys, _autostart = fakes
    app = _build_app(qapp, fakes)
    assert capturer.include_cursor is False

    monkeypatch.setattr(app_module, "SettingsDialog", _FakeSettingsDialog)
    app._open_settings()
    # Simulate the user turning the cursor toggle on, then pressing OK.
    config.set_include_cursor(True)
    app._settings_dialog.accept()
    assert capturer.include_cursor is True
    app.shutdown()


def test_open_settings_reapplies_on_accept(qapp, config, fakes, monkeypatch):
    app = _build_app(qapp, fakes)

    monkeypatch.setattr(app_module, "SettingsDialog", _FakeSettingsDialog)
    rebuilt = []
    monkeypatch.setattr(app, "_rebuild_menu", lambda: rebuilt.append(True))
    monkeypatch.setattr(app, "_apply_hotkeys", lambda: rebuilt.append("hotkeys"))
    app._open_settings()
    app._settings_dialog.accept()
    assert "hotkeys" in rebuilt and True in rebuilt
    app.shutdown()


def test_settings_dialog_is_modeless_and_reused(qapp, config, fakes, monkeypatch):
    # exec() would make the dialog application-modal, which macOS floats above
    # every other app's windows — it must open modeless. A second menu trigger
    # re-fronts the open dialog instead of stacking another one; cancelling
    # drops the reference without re-applying settings.
    app = _build_app(qapp, fakes)
    monkeypatch.setattr(app_module, "SettingsDialog", _FakeSettingsDialog)

    app._open_settings()
    first = app._settings_dialog
    assert first.isVisible()
    assert first.isModal() is False

    app._open_settings()
    assert app._settings_dialog is first  # reused, not replaced

    applied = []
    monkeypatch.setattr(app, "_apply_settings", lambda: applied.append(True))
    first.reject()
    assert app._settings_dialog is None
    assert applied == []
    app.shutdown()


def test_smart_capture_shelves_open_settings_until_overlay_closes(qapp, config, fakes, monkeypatch):
    # A modeless Settings window fights the overlay for activation (the
    # overlay cancels itself when deactivated) and would appear in the shot:
    # capturing must hide it, then bring it back once the overlay is gone.
    from PySide6.QtCore import QEvent

    app = _build_app(qapp, fakes)
    monkeypatch.setattr(app_module, "SettingsDialog", _FakeSettingsDialog)
    app._open_settings()
    dialog = app._settings_dialog
    assert dialog.isVisible()

    app._capture_smart()
    assert not dialog.isVisible()  # shelved for the duration of the capture
    overlay = next(w for w in app._windows if isinstance(w, app_module.SmartOverlay))
    overlay.close()  # every accept/cancel path ends in close()
    qapp.sendPostedEvents(None, QEvent.DeferredDelete)  # let WA_DeleteOnClose land
    assert dialog.isVisible()  # restored, not closed: edits survive
    app.shutdown()


def test_smart_capture_releases_fullscreen_snapshot_when_overlay_closes(qapp, config, fakes):
    from PySide6.QtCore import QEvent

    app = _build_app(qapp, fakes)
    app._capture_smart()
    assert app._smart_screenshot is not None
    assert app._smart_geometry is not None

    overlay = next(w for w in app._windows if isinstance(w, app_module.SmartOverlay))
    overlay.close()
    qapp.sendPostedEvents(None, QEvent.DeferredDelete)

    assert app._smart_screenshot is None
    assert app._smart_geometry is None
    app.shutdown()


def test_fullscreen_capture_shelves_settings_during_the_grab(qapp, config, fakes, monkeypatch):
    capturer, _hotkeys, _autostart = fakes
    app = _build_app(qapp, fakes)
    monkeypatch.setattr(app_module, "SettingsDialog", _FakeSettingsDialog)
    app._open_settings()
    dialog = app._settings_dialog

    seen = []
    original = capturer.capture_fullscreen
    monkeypatch.setattr(
        capturer,
        "capture_fullscreen",
        lambda *a, **k: seen.append(dialog.isVisible()) or original(),
    )
    monkeypatch.setattr(app, "_deliver_capture", lambda *args: None)
    app._capture_fullscreen()
    assert seen == [False]  # hidden while the screen was grabbed
    assert dialog.isVisible()  # and back right after
    app.shutdown()


def test_failed_smart_grab_restores_shelved_settings(qapp, config, fakes, monkeypatch):
    capturer, _hotkeys, _autostart = fakes
    capturer.fail = True
    app = _build_app(qapp, fakes)
    monkeypatch.setattr(app_module, "SettingsDialog", _FakeSettingsDialog)
    monkeypatch.setattr(app_module.QSystemTrayIcon, "showMessage", lambda *args: None)
    app._open_settings()
    dialog = app._settings_dialog

    app._capture_smart()  # grab fails -> no overlay ever opens
    assert dialog.isVisible()
    app.shutdown()


def test_open_save_folder_reveals_configured_dir(qapp, config, fakes, monkeypatch, tmp_path):
    # The menu item creates the save dir if needed (so it works before the
    # first capture) and hands it to the system file manager — `open` on macOS,
    # `xdg-open` on Linux, and ``os.startfile`` (Explorer) on Windows.
    target = tmp_path / "shots"  # does not exist yet
    config.set_save_dir(str(target))
    app = _build_app(qapp, fakes)

    calls = []
    if sys.platform.startswith("win"):
        monkeypatch.setattr(app_module.os, "startfile", lambda p: calls.append(p), raising=False)
        app._open_save_folder()
        assert calls == [str(target)]
    else:
        monkeypatch.setattr(app_module.subprocess, "run", lambda *a, **k: calls.append(a[0]))
        app._open_save_folder()
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        assert calls == [[opener, str(target)]]
    assert target.is_dir()  # created on demand
    app.shutdown()


def test_open_save_folder_notifies_on_failure(qapp, config, fakes, monkeypatch, tmp_path):
    config.set_save_dir(str(tmp_path / "shots"))
    app = _build_app(qapp, fakes)

    def _boom(*args, **kwargs):
        raise OSError("no such volume")

    if sys.platform.startswith("win"):
        monkeypatch.setattr(app_module.os, "startfile", _boom, raising=False)
    else:
        monkeypatch.setattr(app_module.subprocess, "run", _boom)
    notes = []
    monkeypatch.setattr(app, "_notify", lambda msg: notes.append(msg))
    app._open_save_folder()

    assert notes and "no such volume" in notes[0]
    app.shutdown()


# --- editor shell routing: spotlight surface vs framed window ----------------


def _fake_screen(rect):
    class _Screen:
        def geometry(self):
            return rect

    return _Screen()


def test_make_editor_uses_spotlight_for_a_single_screen_region(qapp, config, fakes, monkeypatch):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage

    from shotquill.ui.spotlight import SpotlightSurface

    app = _build_app(qapp, fakes)
    # The shot sits well inside one screen, and spotlight mode is on (default).
    monkeypatch.setattr(qapp, "screenAt", lambda pt: _fake_screen(QRect(0, 0, 1920, 1080)))
    origin = QRect(100, 100, 300, 200)
    editor = app._make_editor(QImage(300, 200, QImage.Format.Format_ARGB32), origin, None)
    try:
        assert isinstance(editor, SpotlightSurface)
    finally:
        editor.close()


def test_make_editor_uses_framed_window_when_backdrop_off(qapp, config, fakes, monkeypatch):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage

    from shotquill.ui.editor import EditorWindow

    config.set_editor_backdrop(False)
    app = _build_app(qapp, fakes)
    monkeypatch.setattr(qapp, "screenAt", lambda pt: _fake_screen(QRect(0, 0, 1920, 1080)))
    origin = QRect(100, 100, 300, 200)
    editor = app._make_editor(QImage(300, 200, QImage.Format.Format_ARGB32), origin, None)
    try:
        assert isinstance(editor, EditorWindow)
    finally:
        editor.close()


def test_make_editor_uses_framed_window_for_a_multi_screen_shot(qapp, config, fakes, monkeypatch):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage

    from shotquill.ui.editor import EditorWindow

    app = _build_app(qapp, fakes)
    # The shot spans beyond the screen the surface could cover (e.g. fullscreen
    # across displays): no single screen contains it, so use the framed window.
    monkeypatch.setattr(qapp, "screenAt", lambda pt: _fake_screen(QRect(0, 0, 640, 480)))
    origin = QRect(100, 100, 2000, 1200)
    editor = app._make_editor(QImage(2000, 1200, QImage.Format.Format_ARGB32), origin, None)
    try:
        assert isinstance(editor, EditorWindow)
    finally:
        editor.close()


def test_apply_settings_reloads_finish_keys_on_both_shells(qapp, config, fakes, monkeypatch):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage

    from shotquill.ui.editor import EditorWindow
    from shotquill.ui.spotlight import SpotlightSurface

    app = _build_app(qapp, fakes)
    monkeypatch.setattr(qapp, "screenAt", lambda pt: _fake_screen(QRect(0, 0, 1920, 1080)))
    origin = QRect(100, 100, 300, 200)
    surface = SpotlightSurface(QImage(300, 200, QImage.Format.Format_ARGB32), config, origin, None)
    window = EditorWindow(QImage(300, 200, QImage.Format.Format_ARGB32), config, origin, None)
    app._track(surface)
    app._track(window)
    reloaded = []
    monkeypatch.setattr(surface, "reload_finish_keys", lambda: reloaded.append("surface"))
    monkeypatch.setattr(window, "reload_finish_keys", lambda: reloaded.append("window"))
    try:
        app._apply_settings()
        assert set(reloaded) == {"surface", "window"}
    finally:
        surface.close()
        window.close()


def test_make_editor_uses_framed_window_on_wayland(qapp, config, fakes, monkeypatch):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage

    from shotquill.ui.editor import EditorWindow

    # Wayland's compositor ignores a set window position, so the spotlight
    # surface can't align with the shot — fall back to the framed window even
    # for a single-screen region with the backdrop on.
    app = _build_app(qapp, fakes)
    monkeypatch.setattr(qapp, "screenAt", lambda pt: _fake_screen(QRect(0, 0, 1920, 1080)))
    monkeypatch.setattr(qapp, "platformName", lambda: "wayland")
    origin = QRect(100, 100, 300, 200)
    editor = app._make_editor(QImage(300, 200, QImage.Format.Format_ARGB32), origin, None)
    try:
        assert isinstance(editor, EditorWindow)
    finally:
        editor.close()


# --- long screenshot (scrolling) ---------------------------------------------


class _FakeScroller:
    def __init__(self):
        self.calls = []
        self.closed = 0

    def scroll(self, clicks, *, at=None):
        self.calls.append((clicks, at))

    def close(self):
        self.closed += 1


def _scroll_results(width, frame_h, offsets):
    """Pre-baked region grabs: distinct-per-row crops of a tall virtual page."""

    def _res(start):
        seeds = list(range(start, start + frame_h))
        px = bytearray()
        for s in seeds:
            px += bytes([s % 251, (s * 7) % 251, (s * 13) % 251, 255]) * width
        return CaptureResult(width=width, height=frame_h, scale=1.0, pixels=bytes(px))

    return [_res(off) for off in offsets]


def test_scrolling_region_collects_frames_and_delivers(qapp, config, fakes, monkeypatch):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage

    from shotquill import allowlist as al

    app = _build_app(qapp, fakes)
    app._allowlist = al.Allowlist()  # disabled
    capturer = fakes[0]
    results = _scroll_results(10, 30, (0, 10, 20))
    state = {"i": 0}

    def _capture_region(region):
        result = results[min(state["i"], len(results) - 1)]
        state["i"] += 1
        return result

    monkeypatch.setattr(capturer, "capture_region", _capture_region)
    scroller = _FakeScroller()
    monkeypatch.setattr(app_module, "get_scroller", lambda: scroller)
    delivered = []
    monkeypatch.setattr(app, "_deliver_capture", lambda image, *a, **k: delivered.append(image))

    app._scrolling_region_selected(QImage(), QRect(0, 0, 10, 30))
    # The QTimer is never spun in the test; drive ticks by hand until delivery.
    for _ in range(20):
        if delivered:
            break
        app._scrolling_tick()

    assert delivered, "the long screenshot was never delivered"
    assert delivered[0].height() == 50  # 30 + 10 + 10 stitched
    assert scroller.calls  # the wheel was turned to advance the page
    assert all(clicks < 0 for clicks, _at in scroller.calls)  # downward
    assert scroller.closed == 1  # pointer/input resources restored
    assert app._scroll is None  # state torn down after delivery
    app.shutdown()


def test_scrolling_region_refused_when_allowlist_enabled(qapp, config, fakes, monkeypatch):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage

    from shotquill import allowlist as al
    from shotquill import blocklist as bl

    app = _build_app(qapp, fakes)
    app._allowlist = al.Allowlist(enabled=True, rules=(bl.BlockRule(name="terminal"),))
    delivered, notified = [], []
    monkeypatch.setattr(app, "_deliver_capture", lambda *a, **k: delivered.append(a))
    monkeypatch.setattr(app, "_notify", notified.append)
    app._scrolling_region_selected(QImage(), QRect(0, 0, 10, 30))
    assert not delivered
    assert notified
    assert app._scroll is None
    app.shutdown()


def test_scrolling_unavailable_when_scroller_refused(qapp, config, fakes, monkeypatch):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage

    from shotquill import allowlist as al

    app = _build_app(qapp, fakes)
    app._allowlist = al.Allowlist()  # disabled

    def _refuse():
        raise app_module.CapabilityUnsupported("auto-scroll", "Wayland blocks synthetic input")

    monkeypatch.setattr(app_module, "get_scroller", _refuse)
    notified = []
    monkeypatch.setattr(app, "_notify", notified.append)
    app._scrolling_region_selected(QImage(), QRect(0, 0, 10, 30))
    assert notified
    assert app._scroll is None  # nothing started
    app.shutdown()


def test_scrolling_needs_region_notifies(qapp, config, fakes, monkeypatch):
    from PySide6.QtCore import QRect

    app = _build_app(qapp, fakes)
    notified = []
    monkeypatch.setattr(app, "_notify", notified.append)
    app._scrolling_needs_region(5, QRect(0, 0, 2, 2))  # window_selected shape
    app._scrolling_needs_region()  # fullscreen_selected shape
    assert len(notified) == 2
    app.shutdown()


def test_scrolling_menu_reports_unavailable_on_wayland_instead_of_taking_one_shot(
    qapp, config, fakes, monkeypatch
):
    app = _build_app(qapp, fakes)
    monkeypatch.setattr(qapp, "platformName", lambda: "wayland")
    portal_calls = []
    monkeypatch.setattr(
        app_module.headless,
        "perform_interactive_capture",
        lambda *args, **kwargs: portal_calls.append(True),
    )
    notified = []
    monkeypatch.setattr(app, "_notify", notified.append)

    app._capture_scrolling()

    assert not portal_calls
    assert notified
    assert app._scroll is None
    app.shutdown()


def test_scrolling_fails_closed_before_capture_when_blocklist_cannot_be_checked(
    qapp, config, fakes, monkeypatch
):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage

    app = _build_app(qapp, fakes)
    active = bl.Blocklist(rules=(bl.BlockRule(name="secret"),))
    app._allowlist = app_module.al.Allowlist()
    monkeypatch.setattr(app, "_load_blocklist_or_abort", lambda: active)
    monkeypatch.setattr(app_module, "get_scroller", _FakeScroller)
    captures = []
    monkeypatch.setattr(
        fakes[0],
        "capture_region",
        lambda region: captures.append(region) or _scroll_results(10, 30, (0,))[0],
    )

    def _cannot_list():
        raise RuntimeError("window server unavailable")

    monkeypatch.setattr(fakes[0], "list_windows", _cannot_list)
    delivered, notified = [], []
    monkeypatch.setattr(app, "_deliver_capture", lambda *args, **kwargs: delivered.append(args))
    monkeypatch.setattr(app, "_notify", notified.append)

    app._scrolling_region_selected(QImage(), QRect(0, 0, 10, 30))
    app._scrolling_tick()

    assert not captures  # policy is checked before raw pixels are grabbed
    assert not delivered
    assert notified
    assert app._scroll is None
    app.shutdown()


def test_scrolling_keeps_settings_shelved_until_session_ends(qapp, config, fakes, monkeypatch):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage

    app = _build_app(qapp, fakes)
    monkeypatch.setattr(app_module, "SettingsDialog", _FakeSettingsDialog)
    monkeypatch.setattr(app_module, "get_scroller", _FakeScroller)
    app._open_settings()
    dialog = app._settings_dialog
    app._shelve_settings_dialog()
    assert not dialog.isVisible()

    app._scrolling_region_selected(QImage(), QRect(0, 0, 10, 30))
    app._unshelve_settings_dialog()  # overlay destruction callback
    assert not dialog.isVisible()

    scroller = app._scroll.scroller
    app._end_scrolling()
    assert dialog.isVisible()
    assert scroller.closed == 1
    app.shutdown()


def test_scrolling_runtime_input_failure_stops_and_notifies(qapp, config, fakes, monkeypatch):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage

    class _FailingScroller(_FakeScroller):
        def scroll(self, clicks, *, at=None):
            raise RuntimeError("input permission revoked")

    app = _build_app(qapp, fakes)
    app._allowlist = app_module.al.Allowlist()
    monkeypatch.setattr(app_module, "get_scroller", _FailingScroller)
    monkeypatch.setattr(fakes[0], "capture_region", lambda region: _scroll_results(10, 30, (0,))[0])
    notified = []
    monkeypatch.setattr(app, "_notify", notified.append)

    app._scrolling_region_selected(QImage(), QRect(0, 0, 10, 30))
    app._scrolling_tick()

    assert notified
    assert app._scroll is None
    app.shutdown()


def test_scrolling_action_retrigger_cancels_active_session(qapp, config, fakes, monkeypatch):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage

    app = _build_app(qapp, fakes)
    app._allowlist = app_module.al.Allowlist()
    scroller = _FakeScroller()
    monkeypatch.setattr(app_module, "get_scroller", lambda: scroller)
    opened = []
    monkeypatch.setattr(app, "_open_smart_overlay", lambda **kwargs: opened.append(kwargs))
    app._scrolling_region_selected(QImage(), QRect(0, 0, 10, 30))

    app._capture_scrolling()

    assert app._scroll is None
    assert scroller.closed == 1
    assert not opened
    app.shutdown()


def test_escape_cancels_active_scrolling_session(qapp, config, fakes, monkeypatch):
    from PySide6.QtCore import QEvent, QRect
    from PySide6.QtGui import QImage, QKeyEvent

    app = _build_app(qapp, fakes)
    app._allowlist = app_module.al.Allowlist()
    scroller = _FakeScroller()
    monkeypatch.setattr(app_module, "get_scroller", lambda: scroller)
    app._scrolling_region_selected(QImage(), QRect(0, 0, 10, 30))

    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    assert app.eventFilter(app, event) is True
    assert app._scroll is None
    assert scroller.closed == 1
    app.shutdown()
