# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Headless tests for the smart-capture overlay.

Drives the overlay through its event handlers (rather than real OS mouse input,
which is unreliable for a frameless full-screen widget under offscreen Qt) and
asserts which capture mode each gesture resolves to: drag -> region, click on a
hovered window -> that window, click on empty space -> full screen.
"""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent, QPixmap  # noqa: E402

from shotquill.capture.base import Rect, WindowInfo  # noqa: E402
from shotquill.ui.smart_overlay import SmartOverlay  # noqa: E402


def _screenshot(width=200, height=100, color="white") -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    return image


def _windows():
    # One window occupying the right half of a 100x50 logical desktop.
    return [WindowInfo(window_id=42, owner="Demo", title="Doc", bounds=Rect(50, 0, 50, 50))]


def _overlay(qtbot, native=(200, 100), logical=(100, 50), windows=None, window_preview=None, **kw):
    # Native screenshot is 2x the logical geometry -> sx = sy = 2.0.
    overlay = SmartOverlay(
        _screenshot(*native),
        QRect(0, 0, *logical),
        windows if windows is not None else [],
        window_preview=window_preview,
        **kw,
    )
    overlay.setAttribute(Qt.WA_DeleteOnClose, False)
    qtbot.addWidget(overlay)
    return overlay


def _mouse(event_type, x, y, button, buttons):
    pos = QPointF(x, y)
    # Use the (local, global, ...) form; the shorter overload is deprecated.
    return QMouseEvent(event_type, pos, pos, button, buttons, Qt.NoModifier)


def _press(overlay, x, y, button=Qt.LeftButton):
    overlay.mousePressEvent(_mouse(QEvent.MouseButtonPress, x, y, button, button))


def _move(overlay, x, y, buttons=Qt.NoButton):
    overlay.mouseMoveEvent(_mouse(QEvent.MouseMove, x, y, Qt.NoButton, buttons))


def _release(overlay, x, y):
    overlay.mouseReleaseEvent(_mouse(QEvent.MouseButtonRelease, x, y, Qt.LeftButton, Qt.LeftButton))


def _hover(overlay, x, y):
    """Move the pointer and land the highlight switch, whatever the configured
    delay — as a pointer rest (or, under NEVER, a press) would."""
    _move(overlay, x, y)
    if overlay._pending_hover != overlay._hover:
        overlay._hover_timer.stop()
        overlay._commit_hover()


def _key(overlay, key, modifiers=Qt.NoModifier):
    overlay.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, modifiers))


def _drag_region(overlay, x0, y0, x1, y1):
    _press(overlay, x0, y0)
    _move(overlay, x1, y1, buttons=Qt.LeftButton)
    _release(overlay, x1, y1)


def test_drag_emits_region_crop_scaled_to_native(qtbot):
    overlay = _overlay(qtbot)
    received = []
    overlay.region_selected.connect(lambda image, rect: received.append((image, rect)))

    # Releasing the drag captures immediately — any pixel fix-up happens in
    # the editor, which keeps the crop arrow-key adjustable.
    _drag_region(overlay, 10, 10, 40, 30)

    assert len(received) == 1
    image, rect = received[0]
    # Logical 30x20 selection -> native 60x40 at the 2x scale.
    assert (image.width(), image.height()) == (60, 40)
    # The rect stays in logical, global coordinates so the editor can reopen
    # the shot in place.
    assert rect == QRect(10, 10, 30, 20)


def test_region_rect_is_translated_to_global_coordinates(qtbot):
    # A virtual desktop whose origin is not (0, 0) — e.g. a screen arranged to
    # the right of another. The emitted rect must be shifted back to global.
    overlay = SmartOverlay(_screenshot(), QRect(100, 200, 100, 50), [])
    overlay.setAttribute(Qt.WA_DeleteOnClose, False)
    qtbot.addWidget(overlay)
    received = []
    overlay.region_selected.connect(lambda image, rect: received.append(rect))

    _drag_region(overlay, 10, 10, 40, 30)

    assert received == [QRect(110, 210, 30, 20)]


def test_click_on_empty_space_emits_fullscreen(qtbot):
    overlay = _overlay(qtbot, windows=_windows())
    fullscreen = []
    overlay.fullscreen_selected.connect(lambda: fullscreen.append(True))

    # Pointer sits in the empty left half, then a click with no drag.
    _move(overlay, 10, 10)
    _press(overlay, 10, 10)
    _release(overlay, 10, 10)

    assert fullscreen == [True]


def test_click_on_window_emits_its_id_and_bounds(qtbot):
    overlay = _overlay(qtbot, windows=_windows())
    received = []
    overlay.window_selected.connect(lambda window_id, rect: received.append((window_id, rect)))

    # Move onto the right-half window, then click without dragging.
    _move(overlay, 70, 25)
    _press(overlay, 70, 25)
    _release(overlay, 70, 25)

    assert received == [(42, QRect(50, 0, 50, 50))]


def test_tiny_move_counts_as_click_not_drag(qtbot):
    overlay = _overlay(qtbot, windows=_windows())
    region = []
    windows = []
    overlay.region_selected.connect(lambda image, rect: region.append((image, rect)))
    overlay.window_selected.connect(lambda window_id, rect: windows.append(window_id))

    _move(overlay, 70, 25)
    _press(overlay, 70, 25)
    _move(overlay, 71, 26, buttons=Qt.LeftButton)  # within the drag threshold
    _release(overlay, 71, 26)

    assert region == []
    assert windows == [42]


def test_crossing_drag_threshold_repaints_the_whole_overlay(qtbot, monkeypatch):
    overlay = _overlay(qtbot, windows=_windows())
    refreshes = []
    monkeypatch.setattr(overlay, "_refresh", lambda dirty=None: refreshes.append(dirty))

    _press(overlay, 10, 10)
    refreshes.clear()  # Ignore the full repaint requested by the initial press.

    _move(overlay, 20, 20, buttons=Qt.LeftButton)

    assert overlay._dragging is True
    # Entering region mode changes the backdrop outside the selection too, so
    # the transition cannot be safely limited to the old/new selection bounds.
    assert refreshes == [None]

    _move(overlay, 21, 21, buttons=Qt.LeftButton)
    assert refreshes[-1] is not None  # Subsequent moves keep the fast dirty path.


def test_highlight_switch_waits_for_pointer_rest(qtbot):
    overlay = _overlay(qtbot, windows=_windows(), hover_switch_delay_ms=3000)

    # Sweeping onto the window arms the switch but does not relight yet.
    _move(overlay, 70, 25)
    assert overlay._hover is None
    assert overlay._hover_timer.isActive()

    # Resting (the timer firing) commits the switch.
    overlay._hover_timer.stop()
    overlay._commit_hover()
    assert overlay._hover == 0

    # Sweeping back off is debounced the same way.
    _move(overlay, 10, 10)
    assert overlay._hover == 0
    assert overlay._hover_timer.isActive()


def test_returning_to_current_target_cancels_pending_switch(qtbot):
    overlay = _overlay(qtbot, windows=_windows(), hover_switch_delay_ms=3000)
    _hover(overlay, 70, 25)

    # Briefly straying off the window and coming back must not flicker the
    # highlight: the pending switch to full screen is dropped.
    _move(overlay, 10, 10)
    assert overlay._hover_timer.isActive()
    _move(overlay, 70, 25)
    assert not overlay._hover_timer.isActive()
    assert overlay._hover == 0


def test_zero_delay_switches_highlight_immediately(qtbot):
    overlay = _overlay(qtbot, windows=_windows(), hover_switch_delay_ms=0)

    _move(overlay, 70, 25)
    assert overlay._hover == 0  # no debounce: relit on the spot
    assert not overlay._hover_timer.isActive()
    _move(overlay, 10, 10)
    assert overlay._hover is None


def test_never_delay_only_switches_highlight_on_press(qtbot):
    from shotquill.config import HOVER_SWITCH_NEVER

    overlay = _overlay(qtbot, windows=_windows(), hover_switch_delay_ms=HOVER_SWITCH_NEVER)
    received = []
    overlay.window_selected.connect(lambda window_id, rect: received.append(window_id))

    # Hovering never relights, no matter how long the pointer rests.
    _move(overlay, 70, 25)
    assert overlay._hover is None
    assert not overlay._hover_timer.isActive()

    # A click still selects the window under the cursor.
    _press(overlay, 70, 25)
    assert overlay._hover == 0
    _release(overlay, 70, 25)
    assert received == [42]


def test_press_settles_pending_hover_so_quick_clicks_hit_the_window(qtbot):
    overlay = _overlay(qtbot, windows=_windows(), hover_switch_delay_ms=3000)
    received = []
    overlay.window_selected.connect(lambda window_id, rect: received.append(window_id))

    # Move onto the window and click before the debounce delay elapses.
    _move(overlay, 70, 25)
    assert overlay._hover is None
    _press(overlay, 70, 25)
    _release(overlay, 70, 25)

    assert received == [42]


def test_enter_settles_pending_hover_like_a_click(qtbot):
    # Enter must confirm what the pointer (and the thin pending outline) is
    # on, even when the debounced highlight hasn't caught up yet — the same
    # rule a press follows.
    overlay = _overlay(qtbot, windows=_windows(), hover_switch_delay_ms=3000)
    received = []
    overlay.window_selected.connect(lambda window_id, rect: received.append(window_id))

    _move(overlay, 70, 25)
    assert overlay._hover is None
    _key(overlay, Qt.Key_Return)

    assert received == [42]


def test_accepting_suppresses_the_deactivation_cancel(qtbot):
    # Opening the editor right after an accept deactivates the overlay before
    # its close lands; that deactivation must not fire a second outcome.
    overlay = _overlay(qtbot)
    cancelled = []
    overlay.cancelled.connect(lambda: cancelled.append(True))
    overlay._activated = True  # as if the overlay had been the active window

    _drag_region(overlay, 10, 10, 40, 30)
    overlay.changeEvent(QEvent(QEvent.ActivationChange))  # editor stole focus

    assert cancelled == []


def test_right_click_cancels(qtbot):
    overlay = _overlay(qtbot)
    cancelled = []
    overlay.cancelled.connect(lambda: cancelled.append(True))
    _press(overlay, 10, 10, button=Qt.RightButton)
    assert cancelled == [True]


def test_escape_cancels(qtbot):
    overlay = _overlay(qtbot)
    cancelled = []
    overlay.cancelled.connect(lambda: cancelled.append(True))
    overlay.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert cancelled == [True]


def test_paint_before_interaction_does_not_crash(qtbot):
    overlay = _overlay(qtbot, windows=_windows())
    overlay.resize(100, 50)
    overlay.repaint()  # no hover, no drag -> full-screen hint path
    assert overlay._hover is None
    assert overlay._cursor is None  # no move yet -> no loupe


def test_move_tracks_cursor_for_loupe(qtbot):
    overlay = _overlay(qtbot, windows=_windows())
    _move(overlay, 70, 25)
    assert overlay._cursor is not None
    assert (overlay._cursor.x(), overlay._cursor.y()) == (70, 25)


def test_leave_hides_loupe(qtbot):
    overlay = _overlay(qtbot)
    _move(overlay, 10, 10)
    overlay.leaveEvent(QEvent(QEvent.Leave))
    assert overlay._cursor is None


def test_paint_with_loupe_does_not_crash(qtbot):
    overlay = _overlay(qtbot, windows=_windows())
    overlay.resize(100, 50)
    _hover(overlay, 70, 25)  # hover path with loupe
    overlay.repaint()
    _press(overlay, 10, 10)
    _move(overlay, 40, 30, buttons=Qt.LeftButton)  # drag path with loupe
    overlay.repaint()


def test_hover_arms_preview_timer_and_leaving_disarms(qtbot):
    overlay = _overlay(qtbot, windows=_windows(), window_preview=lambda wid: _screenshot())

    _hover(overlay, 70, 25)  # onto the window
    assert overlay._preview_timer.isActive()
    _hover(overlay, 10, 10)  # off again before the timer fires -> no fetch
    assert not overlay._preview_timer.isActive()


def test_no_provider_never_arms_preview_timer(qtbot):
    overlay = _overlay(qtbot, windows=_windows())
    _hover(overlay, 70, 25)
    assert not overlay._preview_timer.isActive()


def test_preview_fetch_caches_result_from_worker_thread(qtbot):
    calls = []

    def provider(window_id):
        calls.append(window_id)
        return _screenshot(100, 100, "red")

    overlay = _overlay(qtbot, windows=_windows(), window_preview=provider)
    _hover(overlay, 70, 25)
    with qtbot.waitSignal(overlay._preview_ready, timeout=2000):
        overlay._request_preview()  # fire without waiting out the hover delay

    assert calls == [42]
    assert overlay._previews[42] is not None
    # Hovering the same window again must not refetch.
    _hover(overlay, 10, 10)
    _hover(overlay, 70, 25)
    assert not overlay._preview_timer.isActive()
    assert calls == [42]


def test_failed_preview_is_remembered_and_paint_falls_back(qtbot):
    overlay = _overlay(qtbot, windows=_windows(), window_preview=lambda wid: None)
    overlay.resize(100, 50)
    _hover(overlay, 70, 25)
    with qtbot.waitSignal(overlay._preview_ready, timeout=2000):
        overlay._request_preview()

    assert overlay._previews[42] is None
    overlay.repaint()  # window path without a preview -> frozen screenshot
    # A failure is not retried on the next hover.
    _hover(overlay, 10, 10)
    _hover(overlay, 70, 25)
    assert not overlay._preview_timer.isActive()


def test_painted_window_uses_unoccluded_preview_pixels(qtbot):
    # A big overlay so the loupe and labels stay clear of the probed pixel.
    window = WindowInfo(window_id=7, owner="Demo", title="", bounds=Rect(200, 0, 200, 200))
    overlay = _overlay(
        qtbot, native=(800, 400), logical=(400, 200), windows=[window], window_preview=None
    )
    overlay.resize(400, 200)
    _hover(overlay, 250, 100)

    # Without a preview the (white) frozen screenshot shows through.
    before = overlay.grab().toImage().pixelColor(220, 180)
    assert before.red() == before.green() == before.blue() == 255

    # Inject a fetched preview as the worker handoff would deliver it.
    overlay._on_preview_ready(7, _screenshot(100, 100, "red"))
    after = overlay.grab().toImage().pixelColor(220, 180)
    assert (after.red(), after.green(), after.blue()) == (255, 0, 0)


def test_preview_cache_is_bounded(qtbot):
    windows = [
        WindowInfo(window_id=i, owner=f"Demo {i}", title="", bounds=Rect(i * 10, 0, 10, 10))
        for i in range(4)
    ]
    overlay = _overlay(qtbot, windows=windows)

    for window in windows:
        overlay._on_preview_ready(window.window_id, _screenshot(10, 10, "red"))

    assert len(overlay._previews) <= 3
    assert 0 not in overlay._previews
    assert set(overlay._previews) == {1, 2, 3}


def test_close_releases_controller_cycle_and_pixel_buffers(qtbot):
    overlay = _overlay(qtbot, windows=_windows(), window_preview=lambda wid: _screenshot())
    overlay._on_preview_ready(42, _screenshot(10, 10, "red"))

    class _Controller:
        def __init__(self, brain):
            self._brain = brain
            self.released = False

        def release(self):
            self.released = True
            self._brain = None

    controller = _Controller(overlay)
    overlay._controller = controller

    overlay.close()

    assert controller.released is True
    assert controller._brain is None
    assert getattr(overlay, "_controller", None) is None
    assert overlay._previews == {}
    assert overlay._windows == []
    assert overlay._screenshot.isNull()
    assert overlay._pixmap.isNull()


def test_pointed_window_gets_hairline_before_highlight_switches(qtbot):
    # Pointing must give instant feedback even though the highlight only
    # switches on a click (the default): the window under the pointer is
    # traced with a hairline, without being lit up.
    from shotquill.ui.smart_overlay import _ACCENT

    window = WindowInfo(window_id=7, owner="Demo", title="", bounds=Rect(200, 0, 200, 200))
    overlay = _overlay(qtbot, native=(800, 400), logical=(400, 200), windows=[window])
    overlay.resize(400, 200)

    # Probe the window's left edge, clear of the loupe and the centred hint.
    before = overlay.grab().toImage().pixelColor(200, 20)
    assert before.red() == before.green() == before.blue() == 255

    _move(overlay, 250, 100)
    assert overlay._hover is None  # the highlight itself has not switched
    after = overlay.grab().toImage().pixelColor(200, 20)
    assert (after.red(), after.green(), after.blue()) == (
        _ACCENT.red(),
        _ACCENT.green(),
        _ACCENT.blue(),
    )


def test_pointed_window_is_spotlit_against_dimmed_desktop(qtbot):
    # The pointed window keeps its frozen pixels at full brightness while the
    # rest of the desktop stays dimmed — without the un-occluded preview, so
    # nothing appears to jump to the front.
    window = WindowInfo(window_id=7, owner="Demo", title="", bounds=Rect(200, 0, 200, 200))
    fetched = []
    overlay = _overlay(
        qtbot,
        native=(800, 400),
        logical=(400, 200),
        windows=[window],
        window_preview=lambda wid: fetched.append(wid),
    )
    overlay.resize(400, 200)

    _move(overlay, 250, 100)
    image = overlay.grab().toImage()
    inside = image.pixelColor(220, 150)  # window interior, clear of loupe/outline
    outside = image.pixelColor(100, 150)  # desktop next to it
    assert inside.red() == inside.green() == inside.blue() == 255
    assert outside.red() < 200  # still dimmed
    assert not overlay._preview_timer.isActive() and fetched == []  # no lift-to-front


def test_paint_loupe_at_screen_corner_does_not_crash(qtbot):
    # At (0, 0) the magnified source patch extends past the screenshot and the
    # loupe placement clamps; both paths must stay valid.
    overlay = _overlay(qtbot)
    overlay.resize(100, 50)
    _move(overlay, 0, 0)
    overlay.repaint()
    _move(overlay, 99, 49)  # opposite corner -> anchor flips on both axes
    overlay.repaint()


def test_preview_fetches_are_single_flight(qtbot):
    # Sweeping across windows must not pile one worker thread per window onto
    # the window server: at most one fetch runs; the next is armed on landing.
    import threading

    release = threading.Event()
    calls = []

    def provider(window_id):
        calls.append(window_id)
        release.wait(timeout=5)
        return _screenshot()

    windows = [
        WindowInfo(window_id=42, owner="A", title="", bounds=Rect(0, 0, 40, 50)),
        WindowInfo(window_id=43, owner="B", title="", bounds=Rect(60, 0, 40, 50)),
    ]
    overlay = _overlay(qtbot, windows=windows, window_preview=provider)

    _hover(overlay, 10, 25)  # hover window 42
    overlay._request_preview()  # bypass the hover delay
    _hover(overlay, 70, 25)  # hover window 43 while 42's fetch is stuck
    overlay._request_preview()
    assert calls == [42]  # second fetch deferred, not started

    release.set()
    # 42's fetch lands; the ready handler re-arms for the hovered 43.
    qtbot.waitUntil(lambda: 43 in overlay._previews, timeout=3000)
    assert calls == [42, 43]


def test_reverse_drag_emits_normalized_rect(qtbot):
    overlay = _overlay(qtbot)
    received = []
    overlay.region_selected.connect(lambda image, rect: received.append(rect))
    # Drag from bottom-right to top-left: the emitted rect is normalized.
    _drag_region(overlay, 40, 30, 10, 10)
    assert received == [QRect(10, 10, 30, 20)]


def test_enter_mid_drag_captures_the_region(qtbot):
    overlay = _overlay(qtbot)
    received = []
    overlay.region_selected.connect(lambda image, rect: received.append(rect))
    _press(overlay, 10, 10)
    _move(overlay, 40, 30, buttons=Qt.LeftButton)
    _key(overlay, Qt.Key_Return)
    assert received == [QRect(10, 10, 30, 20)]


# --- keyboard cursor nudging -------------------------------------------------


class _FakeCursor:
    """Stands in for QCursor: setPos cannot drive the real pointer offscreen."""

    position = None

    @classmethod
    def pos(cls):
        from PySide6.QtCore import QPoint

        return QPoint(cls.position)

    @classmethod
    def setPos(cls, pos):
        from PySide6.QtCore import QPoint

        cls.position = QPoint(pos)


def _fake_cursor(monkeypatch, x, y):
    from PySide6.QtCore import QPoint

    from shotquill.ui import smart_overlay as overlay_module

    _FakeCursor.position = QPoint(x, y)
    monkeypatch.setattr(overlay_module, "QCursor", _FakeCursor)
    return _FakeCursor


def test_arrow_keys_nudge_the_pointer_one_point(qtbot, monkeypatch):
    cursor = _fake_cursor(monkeypatch, 50, 25)
    overlay = _overlay(qtbot, windows=_windows())
    _hover(overlay, 70, 25)

    # Steps are based on the overlay's own pointer state (the last move it
    # saw), not QCursor.pos(): the warp's echo may be swallowed (macOS without
    # the Accessibility permission) and must not break key repeats.
    _key(overlay, Qt.Key_Right)
    assert (cursor.position.x(), cursor.position.y()) == (71, 25)
    _key(overlay, Qt.Key_Up)
    assert (cursor.position.x(), cursor.position.y()) == (71, 24)

    # No drag started, and the hover still tracks the (nudged) pointer.
    assert overlay._origin is None
    assert overlay._hover == 0


def test_wasd_nudges_the_pointer_like_arrows(qtbot, monkeypatch):
    cursor = _fake_cursor(monkeypatch, 50, 25)
    overlay = _overlay(qtbot)
    for key, expected in (
        (Qt.Key_A, (49, 25)),
        (Qt.Key_D, (50, 25)),
        (Qt.Key_W, (50, 24)),
        (Qt.Key_S, (50, 25)),
    ):
        _key(overlay, key)
        assert (cursor.position.x(), cursor.position.y()) == expected


def test_shift_nudges_the_pointer_ten_points(qtbot, monkeypatch):
    cursor = _fake_cursor(monkeypatch, 50, 25)
    overlay = _overlay(qtbot)
    _key(overlay, Qt.Key_Left, Qt.ShiftModifier)
    assert (cursor.position.x(), cursor.position.y()) == (40, 25)


def test_nudge_works_mid_drag(qtbot, monkeypatch):
    # Keys keep working while the button is held, so a drag's trailing edge
    # can be landed exactly. The key handler applies the move locally (the
    # warp's echo would land on the same coordinates), so the drag edge
    # follows even when the OS swallows the synthetic move.
    cursor = _fake_cursor(monkeypatch, 40, 30)
    overlay = _overlay(qtbot)
    _press(overlay, 10, 10)
    _move(overlay, 40, 30, buttons=Qt.LeftButton)
    _key(overlay, Qt.Key_Right)
    assert (cursor.position.x(), cursor.position.y()) == (41, 30)
    assert overlay._dragging is True
    assert (overlay._current.x(), overlay._current.y()) == (41, 30)


def test_nudge_drives_the_overlay_without_an_os_echo(qtbot, monkeypatch):
    # The fake cursor never echoes a mouse-move back (like macOS without the
    # Accessibility permission): the overlay's pointer state must still follow
    # the keys, and repeated presses must accumulate rather than re-step from
    # the unmoved real pointer.
    _fake_cursor(monkeypatch, 50, 25)
    overlay = _overlay(qtbot)
    _key(overlay, Qt.Key_Right)  # keys-first: seeds from QCursor.pos()
    _key(overlay, Qt.Key_Right)
    _key(overlay, Qt.Key_Down)
    assert (overlay._cursor.x(), overlay._cursor.y()) == (52, 26)


def test_nudge_ignores_app_shortcut_modifiers(qtbot, monkeypatch):
    # ⌘A / ⌘W (and other modified presses) are app shortcuts, not nudges.
    cursor = _fake_cursor(monkeypatch, 50, 25)
    overlay = _overlay(qtbot)
    _key(overlay, Qt.Key_A, Qt.ControlModifier)
    _key(overlay, Qt.Key_W, Qt.AltModifier)
    assert (cursor.position.x(), cursor.position.y()) == (50, 25)
    assert overlay._cursor is None


def test_overlay_hides_the_os_cursor_for_its_own_crosshair(qtbot):
    # The visible marker is painted (see _paint_cursor), not the OS cursor, so
    # it can track keys the OS won't let us warp the real pointer with.
    overlay = _overlay(qtbot)
    assert overlay.cursor().shape() == Qt.BlankCursor


def test_painted_crosshair_follows_the_keyboard_nudge(qtbot, monkeypatch):
    # On Wayland / macOS-without-Accessibility the real pointer can't be warped,
    # so the painted crosshair is the only thing that moves — it must track the
    # keys, not stay where the OS pointer is stuck.
    _fake_cursor(monkeypatch, 60, 40)
    # A roomy desktop on a black ground so the white crosshair reads cleanly.
    overlay = _overlay(qtbot, native=(800, 400), logical=(400, 200))
    overlay.resize(400, 200)
    overlay._pixmap = QPixmap.fromImage(_screenshot(800, 400, "black"))
    # Suppress the loupe so the only bright marks are the crosshair arms; its
    # frame sits near the pointer and its placement vs. anti-aliasing differs
    # across offscreen/Xvfb. The pointer and probes also stay well away from the
    # centred hint text (~200, 100), whose glyphs render differently per backend.
    monkeypatch.setattr(overlay, "_paint_loupe", lambda painter: None)

    def left_arm_is_bright(cx):
        # (cx - 6, 40) sits on the left arm (spans cx-11 .. cx-3) for any cx.
        c = overlay.grab().toImage().pixelColor(cx - 6, 40)
        return c.red() > 150 and c.green() > 150 and c.blue() > 150

    _move(overlay, 60, 40)
    assert left_arm_is_bright(60)  # crosshair painted at the pointer
    assert not left_arm_is_bright(90)  # nothing there yet

    # Three coarse nudges -> +30px, so the new crosshair clears the old (11px arms).
    for _ in range(3):
        _key(overlay, Qt.Key_Right, Qt.ShiftModifier)
    assert (overlay._cursor.x(), overlay._cursor.y()) == (90, 40)
    assert left_arm_is_bright(90)  # the crosshair has followed the keys
    assert not left_arm_is_bright(60)  # and left the old spot


def test_no_preview_fetch_after_close(qtbot):
    calls = []

    def provider(window_id):
        calls.append(window_id)
        return _screenshot()

    overlay = _overlay(qtbot, windows=_windows(), window_preview=provider)
    _hover(overlay, 70, 25)
    overlay.close()
    overlay._request_preview()  # a queued timer tick after close must no-op
    assert calls == []
    assert not overlay._preview_timer.isActive()


def test_label_font_inherits_ui_font(qtbot):
    # Regression: the overlay labels (size, window, loupe, hint) must render in
    # the real UI font. They used to be built with QFont("", n) — an *empty*
    # family, which doesn't mean "the default font": Qt's matcher is free to
    # resolve it to some unrelated installed face (e.g. an Ethiopic one on bare
    # Linux), giving oddly proportioned glyphs and uneven spacing. _label_font
    # must instead inherit the widget's resolved family and only set the size.
    from PySide6.QtGui import QFontInfo

    overlay = _overlay(qtbot)
    font = overlay._label_font(12)
    assert font.pointSize() == 12
    assert QFontInfo(font).family() == QFontInfo(overlay.font()).family()


# --- presentation (X11/macOS vs Wayland) ----------------------------------


def test_compositor_prefers_fullscreen_follows_platform(monkeypatch):
    from PySide6.QtGui import QGuiApplication

    from shotquill.ui import smart_overlay

    monkeypatch.setattr(QGuiApplication, "platformName", staticmethod(lambda: "wayland"))
    assert smart_overlay._compositor_prefers_fullscreen() is True
    monkeypatch.setattr(QGuiApplication, "platformName", staticmethod(lambda: "xcb"))
    assert smart_overlay._compositor_prefers_fullscreen() is False
    monkeypatch.setattr(QGuiApplication, "platformName", staticmethod(lambda: "offscreen"))
    assert smart_overlay._compositor_prefers_fullscreen() is False


def test_present_uses_plain_show_off_wayland(qtbot, monkeypatch):
    # The default test platform is offscreen -> not Wayland -> normal show path,
    # which must leave the stay-on-top top-level visible (and not fullscreen).
    from shotquill.ui import smart_overlay

    monkeypatch.setattr(smart_overlay, "_compositor_prefers_fullscreen", lambda: False)
    overlay = _overlay(qtbot)
    calls = []
    monkeypatch.setattr(overlay, "show", lambda: calls.append("show"))
    monkeypatch.setattr(overlay, "showFullScreen", lambda: calls.append("fullscreen"))
    overlay.present()
    assert calls == ["show"]


def test_present_goes_fullscreen_on_wayland(qtbot, monkeypatch):
    # On Wayland the compositor ignores stay-on-top + geometry, so present()
    # must ask for fullscreen instead so the dim layer owns the output.
    from shotquill.ui import smart_overlay

    monkeypatch.setattr(smart_overlay, "_compositor_prefers_fullscreen", lambda: True)
    overlay = _overlay(qtbot)
    calls = []
    monkeypatch.setattr(overlay, "show", lambda: calls.append("show"))
    monkeypatch.setattr(overlay, "showFullScreen", lambda: calls.append("fullscreen"))
    overlay.present()
    assert calls == ["fullscreen"]


def test_present_overlay_uses_fullscreen_controller_on_multi_output_wayland(qtbot, monkeypatch):
    # A Wayland fullscreen surface is per-output, so multi-monitor sessions need
    # one fullscreen view per screen instead of one virtual-desktop top-level.
    from shotquill.ui import smart_overlay

    overlay = _overlay(qtbot)
    calls = []

    class _Controller:
        def __init__(self, brain, *, fullscreen=False):
            calls.append(("controller", brain is overlay, fullscreen))

        def present(self):
            calls.append(("present",))

    class _App:
        def screens(self):
            return [object(), object()]

    monkeypatch.setattr(smart_overlay, "_compositor_prefers_fullscreen", lambda: True)
    monkeypatch.setattr(smart_overlay.sys, "platform", "linux")
    monkeypatch.setattr(smart_overlay, "SmartOverlayController", _Controller)
    monkeypatch.setattr(overlay, "present", lambda: calls.append(("single",)))
    smart_overlay.present_overlay(overlay, _App())
    assert calls == [("controller", True, True), ("present",)]


# --- CropAdjustOverlay: drag edges/corners to fine-tune an existing crop -----

from shotquill.ui.smart_overlay import CropAdjustOverlay  # noqa: E402


def _adjust(qtbot, geometry=None, selection=None, shot=None):
    # sx = sy = 1 by default (native == logical) so emitted dims equal the
    # selection's logical size and assertions stay simple.
    geometry = geometry if geometry is not None else QRect(0, 0, 400, 300)
    selection = selection if selection is not None else QRect(100, 80, 200, 120)
    image = shot if shot is not None else _screenshot(geometry.width(), geometry.height())
    overlay = CropAdjustOverlay(image, geometry, selection)
    overlay.setAttribute(Qt.WA_DeleteOnClose, False)
    qtbot.addWidget(overlay)
    return overlay


def test_adjust_drag_right_edge_grows_width(qtbot):
    overlay = _adjust(qtbot)
    received = []
    overlay.region_selected.connect(lambda image, rect: received.append((image, rect)))

    _press(overlay, 300, 140)  # right-edge midpoint
    _move(overlay, 340, 140, buttons=Qt.LeftButton)
    _release(overlay, 340, 140)
    assert overlay._sel == QRectF(100, 80, 240, 120)

    _key(overlay, Qt.Key_Return)  # apply
    assert len(received) == 1
    image, rect = received[0]
    assert rect == QRect(100, 80, 240, 120)
    assert (image.width(), image.height()) == (240, 120)


def test_adjust_drag_left_edge_reveals_more_of_the_shot(qtbot):
    shot = _screenshot(400, 300)
    shot.setPixelColor(60, 80, QColor("red"))  # pixel exposed only once the left edge moves
    overlay = _adjust(qtbot, shot=shot)
    received = []
    overlay.region_selected.connect(lambda image, rect: received.append((image, rect)))

    _press(overlay, 100, 140)  # left-edge midpoint
    _move(overlay, 60, 140, buttons=Qt.LeftButton)
    _release(overlay, 60, 140)
    assert overlay._sel == QRectF(60, 80, 240, 120)

    _key(overlay, Qt.Key_Return)
    image, rect = received[0]
    assert rect == QRect(60, 80, 240, 120)
    assert image.pixelColor(0, 0) == QColor("red")  # crop now starts at (60,80)


def test_adjust_drag_corner_moves_both_axes(qtbot):
    overlay = _adjust(qtbot)
    _press(overlay, 100, 80)  # top-left corner
    _move(overlay, 90, 70, buttons=Qt.LeftButton)
    _release(overlay, 90, 70)
    assert overlay._sel == QRectF(90, 70, 210, 130)


def test_adjust_drag_inside_moves_the_whole_selection(qtbot):
    overlay = _adjust(qtbot)
    _press(overlay, 200, 140)  # interior, clear of any edge band
    _move(overlay, 210, 150, buttons=Qt.LeftButton)
    _release(overlay, 210, 150)
    assert overlay._sel == QRectF(110, 90, 200, 120)


def test_adjust_clamps_resize_to_the_desktop_bounds(qtbot):
    overlay = _adjust(qtbot)
    _press(overlay, 300, 140)
    _move(overlay, 9999, 140, buttons=Qt.LeftButton)  # drag the right edge off-screen
    _release(overlay, 9999, 140)
    assert overlay._sel == QRectF(100, 80, 300, 120)  # right edge pinned at desktop width 400


def test_adjust_plain_click_inside_applies(qtbot):
    overlay = _adjust(qtbot)
    received = []
    overlay.region_selected.connect(lambda image, rect: received.append(rect))

    _press(overlay, 200, 140)
    _release(overlay, 200, 140)  # no drag -> apply the unchanged selection
    assert received == [QRect(100, 80, 200, 120)]


def test_adjust_escape_cancels(qtbot):
    overlay = _adjust(qtbot)
    cancelled = []
    overlay.cancelled.connect(lambda: cancelled.append(True))
    _key(overlay, Qt.Key_Escape)
    assert cancelled == [True]


def test_adjust_right_click_cancels(qtbot):
    overlay = _adjust(qtbot)
    cancelled = []
    overlay.cancelled.connect(lambda: cancelled.append(True))
    _press(overlay, 200, 140, button=Qt.RightButton)
    assert cancelled == [True]


def test_adjust_press_outside_redraws_a_fresh_selection(qtbot):
    overlay = _adjust(qtbot)
    received = []
    overlay.region_selected.connect(lambda image, rect: received.append(rect))

    _press(overlay, 10, 10)  # outside the current box
    _move(overlay, 50, 40, buttons=Qt.LeftButton)
    _release(overlay, 50, 40)
    assert overlay._sel == QRectF(10, 10, 40, 30)
    _key(overlay, Qt.Key_Return)
    assert received == [QRect(10, 10, 40, 30)]


def test_adjust_seeds_and_emits_in_global_coordinates(qtbot):
    # A virtual desktop whose origin is not (0, 0): the selection is passed in
    # global points and must round-trip back to global on apply.
    overlay = _adjust(
        qtbot, geometry=QRect(100, 200, 400, 300), selection=QRect(120, 220, 200, 120)
    )
    assert overlay._sel == QRectF(20, 20, 200, 120)  # seeded in overlay-local points
    received = []
    overlay.region_selected.connect(lambda image, rect: received.append(rect))
    _press(overlay, 120, 80)  # interior in local coords
    _release(overlay, 120, 80)
    assert received == [QRect(120, 220, 200, 120)]


def test_adjust_keyboard_nudges_the_whole_selection(qtbot):
    overlay = _adjust(qtbot)
    _key(overlay, Qt.Key_Right)
    assert overlay._sel == QRectF(101, 80, 200, 120)
    _key(overlay, Qt.Key_Down, modifiers=Qt.ShiftModifier)
    assert overlay._sel == QRectF(101, 90, 200, 120)


def test_adjust_paint_smoke(qtbot):
    # Exercise the full paint path (lit selection + handles + hint + loupe) so a
    # drawing bug surfaces headlessly; the handlers alone never trigger a repaint.
    from PySide6.QtGui import QPainter

    overlay = _adjust(qtbot)
    overlay._cursor = QPointF(150, 130)  # so guides/crosshair/loupe also draw
    canvas = QPixmap(400, 300)
    painter = QPainter(canvas)
    overlay._paint_all(painter)
    painter.end()
