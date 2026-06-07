# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Headless tests for the top-level application controller.

The platform managers (screen capture, global hotkeys, launch-at-login) are
replaced with fakes (the shared ``fakes`` fixture in conftest) so the
orchestration logic — hotkey registration, the capture success/failure paths,
autostart syncing, window bookkeeping — can be exercised offscreen without
touching real system frameworks.
"""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QDialog  # noqa: E402

from shotquill import app as app_module  # noqa: E402


def _build_app(qapp, fakes):
    return app_module.ShotquillApp(qapp)


def test_build_icon_is_not_null(qapp):
    icon = app_module._build_icon()
    assert not icon.isNull()


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


def test_grab_returns_qimage_on_success(qapp, config, fakes):
    app = _build_app(qapp, fakes)
    image = app._grab()
    assert image is not None
    assert (image.width(), image.height()) == (4, 3)
    app.shutdown()


def test_grab_returns_none_on_capture_failure(qapp, config, fakes):
    capturer, _hotkeys, _autostart = fakes
    app = _build_app(qapp, fakes)
    capturer.fail = True
    assert app._grab() is None  # error is reported via the tray, not raised
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
        app, "_deliver_capture", lambda image, origin=None: delivered.append((image, origin))
    )
    origin = QRect(10, 20, 4, 3)
    app._capture_window_image(42, origin)
    assert len(delivered) == 1
    image, got_origin = delivered[0]
    assert (image.width(), image.height()) == (4, 3)
    assert got_origin == origin
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
    monkeypatch.setattr(app, "_deliver_capture", lambda image, origin=None: delivered.append(image))
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
    app.shutdown()


def test_open_editor_honours_region_adjust_setting(qapp, config, fakes):
    # Turning the Settings toggle off must strip the region context, so the
    # editor opens with a frozen crop (no arrow-key adjustment).
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage

    from shotquill.ui.editor import EditorWindow, RegionContext

    app = _build_app(qapp, fakes)
    image = QImage(4, 3, QImage.Format.Format_ARGB32)
    region = RegionContext(image, QRect(0, 0, 4, 3))

    app._open_editor(image, QRect(0, 0, 4, 3), region)
    adjustable = [w for w in app._windows if isinstance(w, EditorWindow)][-1]
    assert adjustable._region is not None

    config.set_region_adjust(False)
    app._open_editor(image, QRect(0, 0, 4, 3), region)
    frozen = [w for w in app._windows if isinstance(w, EditorWindow)][-1]
    assert frozen._region is None

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

    def __init__(self, cfg):
        super().__init__()


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
        lambda: seen.append(dialog.isVisible()) or original(),
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
