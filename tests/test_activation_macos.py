# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Window-activation scenarios under a real macOS window server.

The headless suite runs on the offscreen platform, which has no window
manager: ``show()`` never triggers activation arbitration between top-level
windows, so bugs in that area — e.g. a modeless Settings window fighting the
capture overlay, whose cancel-on-deactivate guard then wedged the capture —
are invisible there. These tests spin the real Cocoa event loop instead and
assert the capture flow survives it.

They run only where that loop exists: the macOS CI leg (which runs pytest
without ``QT_QPA_PLATFORM``) or a developer's Mac — expect a full-screen
overlay to flash briefly there. Everywhere else they are skipped. Activation
timing on shared CI runners is not fully deterministic; assertions are
arranged so that "never activated" passes quietly (or skips) rather than
flaking, while the regression scenarios still fail loudly.
"""

import os
import sys

import pytest

pytest.importorskip("PySide6")

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("QT_QPA_PLATFORM") == "offscreen",
    reason="needs a real macOS window server for activation arbitration",
)

# Real-loop settle time: long enough for the window server to deliver
# activation changes, short enough to keep the CI leg quick.
_SETTLE_MS = 800


def _build_app(qapp):
    from shotquill import app as app_module

    return app_module.ShotquillApp(qapp)


def _smart_overlay(app):
    from shotquill.ui.smart_overlay import SmartOverlay

    return next((w for w in app._windows if isinstance(w, SmartOverlay)), None)


def test_smart_overlay_survives_the_real_event_loop(qapp, qtbot, config, fakes):
    # Baseline: with nothing else on screen, the overlay's cancel-on-deactivate
    # guard must not fire from ordinary window-server traffic.
    app = _build_app(qapp)
    app._capture_smart()
    overlay = _smart_overlay(app)
    assert overlay is not None

    qtbot.wait(_SETTLE_MS)
    assert _smart_overlay(app) is overlay  # alive: did not cancel itself

    overlay.close()
    app.shutdown()


def test_smart_capture_survives_an_open_settings_window(qapp, qtbot, config, fakes):
    # Regression: the modeless Settings window used to contend with the
    # overlay for activation the moment it appeared; the overlay's guard then
    # cancelled the capture. Capturing now shelves the dialog first.
    app = _build_app(qapp)
    app._open_settings()
    dialog = app._settings_dialog
    qtbot.waitUntil(dialog.isVisible, timeout=3000)
    qtbot.wait(200)  # let the dialog take activation, as a user's would

    app._capture_smart()
    overlay = _smart_overlay(app)
    assert overlay is not None
    assert not dialog.isVisible()  # shelved for the duration of the capture

    qtbot.wait(_SETTLE_MS)  # the window server arbitrates activation here
    assert _smart_overlay(app) is overlay  # alive: capture flow not wedged

    overlay.close()
    # waitUntil spins the real loop, which also delivers the overlay's
    # deferred deletion — that is what triggers the unshelve.
    qtbot.waitUntil(lambda: app._settings_dialog.isVisible(), timeout=3000)
    app.shutdown()


def test_overlay_still_cancels_when_activation_is_genuinely_stolen(qapp, qtbot, config, fakes):
    # The guard itself must keep working: when another window truly takes
    # activation (the real-world stand-in for Mission Control / Cmd-Tab), a
    # screen-covering overlay the user can't dismiss must remove itself.
    from PySide6.QtWidgets import QWidget

    app = _build_app(qapp)
    app._capture_smart()
    overlay = _smart_overlay(app)
    assert overlay is not None

    try:
        qtbot.waitUntil(overlay.isActiveWindow, timeout=3000)
    except TimeoutError:
        overlay.close()
        app.shutdown()
        pytest.skip("window server never activated the overlay; guard not exercisable")

    thief = QWidget()
    qtbot.addWidget(thief)
    thief.show()
    thief.raise_()
    thief.activateWindow()
    qtbot.waitUntil(lambda: _smart_overlay(app) is None, timeout=3000)
    app.shutdown()
