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

from PySide6.QtCore import QEvent, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent  # noqa: E402

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
    """Move the pointer and let the debounced highlight switch land, as a real
    pointer rest would."""
    _move(overlay, x, y)
    if overlay._hover_timer.isActive():
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

    # With adjustment on (the default) the release pins the selection for
    # keyboard nudging; Enter then captures it.
    _drag_region(overlay, 10, 10, 40, 30)
    assert received == []
    assert overlay._pinned is True
    _key(overlay, Qt.Key_Return)

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
    _key(overlay, Qt.Key_Return)

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


def test_highlight_switch_waits_for_pointer_rest(qtbot):
    overlay = _overlay(qtbot, windows=_windows())

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
    overlay = _overlay(qtbot, windows=_windows())
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
    overlay = _overlay(qtbot, windows=_windows())
    received = []
    overlay.window_selected.connect(lambda window_id, rect: received.append(window_id))

    # Move onto the window and click before the debounce delay elapses.
    _move(overlay, 70, 25)
    assert overlay._hover is None
    _press(overlay, 70, 25)
    _release(overlay, 70, 25)

    assert received == [42]


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


# --- pinned-selection keyboard adjustment -----------------------------------


def test_release_pins_selection_and_normalizes_corners(qtbot):
    overlay = _overlay(qtbot)
    # Drag from bottom-right to top-left: pinning must still normalize so
    # _origin is the top-left corner (resizing acts on _current).
    _drag_region(overlay, 40, 30, 10, 10)
    assert overlay._pinned is True
    assert (overlay._origin.x(), overlay._origin.y()) == (10, 10)
    assert (overlay._current.x(), overlay._current.y()) == (40, 30)


def test_region_adjust_off_captures_on_release(qtbot):
    overlay = _overlay(qtbot, region_adjust=False)
    received = []
    overlay.region_selected.connect(lambda image, rect: received.append(rect))
    _drag_region(overlay, 10, 10, 40, 30)
    assert received == [QRect(10, 10, 30, 20)]
    assert overlay._pinned is False


def test_arrow_moves_selection_one_native_pixel(qtbot):
    # sx = sy = 1 here, so one native pixel is one logical point and the
    # emitted rect shifts by exactly 1.
    overlay = _overlay(qtbot, native=(100, 50), logical=(100, 50))
    received = []
    overlay.region_selected.connect(lambda image, rect: received.append(rect))

    _drag_region(overlay, 10, 10, 40, 30)
    _key(overlay, Qt.Key_Right)
    _key(overlay, Qt.Key_Down)
    _key(overlay, Qt.Key_Down)
    _key(overlay, Qt.Key_Return)

    assert received == [QRect(11, 12, 30, 20)]


def test_arrow_step_is_native_not_logical_on_retina(qtbot):
    # At the default 2x scale a press moves the crop by one *screenshot*
    # pixel, i.e. half a logical point — not a whole point.
    overlay = _overlay(qtbot)
    _drag_region(overlay, 10, 10, 40, 30)
    _key(overlay, Qt.Key_Right)
    assert overlay._origin.x() == 10.5
    assert overlay._current.x() == 40.5
    # Width is untouched: both corners moved together.
    assert overlay._current.x() - overlay._origin.x() == 30


def test_shift_arrow_moves_ten_native_pixels(qtbot):
    overlay = _overlay(qtbot, native=(100, 50), logical=(100, 50))
    _drag_region(overlay, 10, 10, 40, 30)
    _key(overlay, Qt.Key_Right, Qt.ShiftModifier)
    assert overlay._origin.x() == 20


def test_alt_arrow_resizes_right_and_bottom_edge(qtbot):
    overlay = _overlay(qtbot, native=(100, 50), logical=(100, 50))
    received = []
    overlay.region_selected.connect(lambda image, rect: received.append(rect))

    _drag_region(overlay, 10, 10, 40, 30)
    _key(overlay, Qt.Key_Right, Qt.AltModifier)  # width +1
    _key(overlay, Qt.Key_Up, Qt.AltModifier)  # height -1
    _key(overlay, Qt.Key_Return)

    assert received == [QRect(10, 10, 31, 19)]


def test_move_clamps_to_screen_edges(qtbot):
    overlay = _overlay(qtbot, native=(100, 50), logical=(100, 50))
    _drag_region(overlay, 10, 10, 40, 30)
    for _ in range(3):
        _key(overlay, Qt.Key_Left, Qt.ShiftModifier)  # 30 px left of x=10
    assert overlay._origin.x() == 0
    assert overlay._current.x() == 30  # width preserved while clamped
    for _ in range(9):
        _key(overlay, Qt.Key_Right, Qt.ShiftModifier)
    assert overlay._current.x() == 100
    assert overlay._origin.x() == 70


def test_resize_clamps_at_min_size_and_screen_edge(qtbot):
    overlay = _overlay(qtbot, native=(100, 50), logical=(100, 50))
    _drag_region(overlay, 10, 10, 40, 30)
    for _ in range(5):
        _key(overlay, Qt.Key_Left, Qt.AltModifier | Qt.ShiftModifier)
    assert overlay._current.x() == 12  # origin + _MIN_SIZE
    for _ in range(10):
        _key(overlay, Qt.Key_Right, Qt.AltModifier | Qt.ShiftModifier)
    assert overlay._current.x() == 100  # right edge of the overlay


def test_arrow_keys_ignored_when_not_pinned(qtbot):
    overlay = _overlay(qtbot, windows=_windows())
    _hover(overlay, 70, 25)
    _key(overlay, Qt.Key_Right)
    assert overlay._origin is None  # nothing selected, nothing moved


def test_keyboard_nudge_parks_loupe_on_the_adjusted_edge(qtbot):
    overlay = _overlay(qtbot, native=(100, 50), logical=(100, 50))
    _drag_region(overlay, 10, 10, 40, 30)
    _key(overlay, Qt.Key_Right)  # move: loupe at the middle of the right edge
    assert (overlay._cursor.x(), overlay._cursor.y()) == (41, 20)
    _key(overlay, Qt.Key_Down, Qt.AltModifier)  # resize: bottom edge
    assert (overlay._cursor.x(), overlay._cursor.y()) == (26, 31)


def test_mouse_move_does_not_disturb_pinned_selection(qtbot):
    overlay = _overlay(qtbot)
    _drag_region(overlay, 10, 10, 40, 30)
    _move(overlay, 80, 45)
    assert (overlay._current.x(), overlay._current.y()) == (40, 30)
    assert overlay._pinned is True


def test_click_inside_pinned_selection_captures(qtbot):
    overlay = _overlay(qtbot)
    received = []
    overlay.region_selected.connect(lambda image, rect: received.append(rect))

    _drag_region(overlay, 10, 10, 40, 30)
    _press(overlay, 25, 20)
    _release(overlay, 25, 20)

    assert received == [QRect(10, 10, 30, 20)]


def test_click_outside_pinned_selection_discards_without_capturing(qtbot):
    overlay = _overlay(qtbot, windows=_windows())
    region = []
    fullscreen = []
    windows = []
    overlay.region_selected.connect(lambda image, rect: region.append(rect))
    overlay.fullscreen_selected.connect(lambda: fullscreen.append(True))
    overlay.window_selected.connect(lambda window_id, rect: windows.append(window_id))

    _drag_region(overlay, 5, 5, 30, 30)
    _press(overlay, 70, 40)  # over the window, outside the selection
    _release(overlay, 70, 40)

    # The pin is dropped, but the click must not capture the window or screen
    # under it — it only dismisses the selection.
    assert overlay._pinned is False
    assert overlay._origin is None
    assert (region, fullscreen, windows) == ([], [], [])


def test_drag_outside_pinned_selection_starts_a_new_one(qtbot):
    overlay = _overlay(qtbot)
    received = []
    overlay.region_selected.connect(lambda image, rect: received.append(rect))

    _drag_region(overlay, 5, 5, 20, 20)
    _drag_region(overlay, 50, 10, 90, 40)  # starts outside the old selection
    assert overlay._pinned is True
    _key(overlay, Qt.Key_Return)

    assert received == [QRect(50, 10, 40, 30)]


def test_escape_while_pinned_cancels(qtbot):
    overlay = _overlay(qtbot)
    cancelled = []
    overlay.cancelled.connect(lambda: cancelled.append(True))
    _drag_region(overlay, 10, 10, 40, 30)
    _key(overlay, Qt.Key_Escape)
    assert cancelled == [True]


def test_paint_pinned_selection_with_hint_does_not_crash(qtbot):
    overlay = _overlay(qtbot)
    overlay.resize(100, 50)
    _drag_region(overlay, 10, 10, 40, 30)
    overlay.repaint()  # pinned path: region + size label + adjust hint
    _drag_region(overlay, 2, 2, 98, 48)  # hint won't fit below -> tucked inside
    overlay.repaint()


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
