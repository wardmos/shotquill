# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Wayland global hotkeys via the xdg-desktop-portal GlobalShortcuts portal.

A Wayland compositor refuses to let an application grab keys out of band — the
exact reason the X11 :mod:`shotquill.hotkeys.linux` backend goes silent there.
The compositor-blessed path is ``org.freedesktop.portal.GlobalShortcuts``: the
app opens a *session*, binds a set of shortcuts (each an id, a human description,
and a *preferred* trigger the compositor may honour or let the user re-bind), and
the portal then emits an ``Activated`` signal carrying the shortcut id whenever
one fires — no key grab, no eavesdropping on other apps' input.

This mirrors :mod:`shotquill.capture.wayland`: the live D-Bus work (open the
session, bind, subscribe, tear down) is isolated in :meth:`_activate` /
:meth:`_deactivate` — the seam tests replace — while the bookkeeping around it
(compiling bindings into portal specs, the id↔combo map, and dispatching an
``Activated`` id back to its callback) is plain logic covered without a live
portal. The QtDBus round-trip itself still needs a real-Wayland smoke.

Unlike pynput, the portal delivers ``Activated`` on QtDBus's event loop, i.e.
the GUI thread, so a callback can run directly — but the app layer still routes
captures through its queued :class:`~shotquill.app._HotkeyBridge` signals, which
is harmless when the emit already happens on the main thread.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from shotquill.hotkeys.base import HotkeyManager, HotkeyUnavailable
from shotquill.hotkeys.combo import portal_shortcut_id, to_portal_trigger

_PORTAL_SERVICE = "org.freedesktop.portal.Desktop"
_PORTAL_PATH = "/org/freedesktop/portal/desktop"
_GLOBALSHORTCUTS_IFACE = "org.freedesktop.portal.GlobalShortcuts"
_REQUEST_IFACE = "org.freedesktop.portal.Request"
# Closing a session uses the generic portal Session interface, not GlobalShortcuts.
_SESSION_IFACE = "org.freedesktop.portal.Session"
# CreateSession and BindShortcuts are Request/Response round-trips. CreateSession
# is silent, but a compositor may surface a one-time prompt to confirm a binding,
# so allow real time before giving up rather than flashing past a dialog.
_PORTAL_TIMEOUT_MS = 30_000


@dataclass
class _Binding:
    combo: str
    callback: Callable[[], None]
    description: str


class WaylandHotkeyManager(HotkeyManager):
    """Global hotkeys through xdg-desktop-portal GlobalShortcuts (Wayland)."""

    def __init__(self) -> None:
        self._bindings: dict[str, _Binding] = {}  # combo -> binding
        # shortcut id (what ``Activated`` carries back) -> combo. Rebuilt on every
        # ``start`` so a removed binding can never resolve to a stale callback.
        self._ids: dict[str, str] = {}
        self._session_handle: str | None = None  # set once the portal session is open

    # --- bookkeeping (no live portal) -------------------------------------

    def register(
        self, combo: str, callback: Callable[[], None], description: str | None = None
    ) -> None:
        self._bindings[combo] = _Binding(combo, callback, description or combo)

    def unregister(self, combo: str) -> None:
        self._bindings.pop(combo, None)

    def clear(self) -> None:
        self._bindings.clear()

    def start(self) -> None:
        """Bind the current shortcuts with the portal (idempotent re-apply).

        Re-applying Settings calls this again; the portal model is one shortcut
        set per session, so a re-bind tears the old session down and opens a
        fresh one — cheap, and unlike the pynput listeners there is no thread to
        crash by restarting. An empty binding set is a no-op (and never opens a
        session): there is nothing to grab and no reason to prompt.

        Raises :class:`HotkeyUnavailable` when the GlobalShortcuts portal is
        missing (no session bus, or a compositor/portal without the interface),
        so the app surfaces the reason once and keeps the tray menu working.

        Unlike the base "non-blocking" contract the pynput backends meet with a
        listener thread, this briefly drives a Qt event loop for the portal's
        CreateSession/BindShortcuts round-trip (capped by ``_PORTAL_TIMEOUT_MS``).
        That is fast on a healthy portal, the common "no portal" failure is
        detected up front with no round-trip at all (a bare ``isValid`` check),
        and the cap bounds a hung one — so a worker thread (with its own QtDBus
        loop) would add fragility for a sub-second wait. Documented here so a
        future maintainer keeps the trade rather than "fixing" it blindly.
        """
        if not self._bindings:
            self.stop()  # a prior set may have been cleared; drop any open session
            return
        specs = [
            (portal_shortcut_id(b.combo), b.combo, to_portal_trigger(b.combo), b.description)
            for b in self._bindings.values()
        ]
        self._ids = {shortcut_id: combo for shortcut_id, combo, _, _ in specs}
        self._activate(specs)

    def stop(self) -> None:
        self._deactivate()
        self._session_handle = None
        # Drop the id→combo map alongside the session. The _on_activated guard
        # already rejects signals once _session_handle is None, but clearing the
        # map keeps the invariant "ids exist only while a session is live", so a
        # stale id can never resolve to a callback even if that guard regresses.
        self._ids = {}

    def _dispatch(self, shortcut_id: str) -> None:
        """Invoke the callback an ``Activated`` shortcut id maps to (no-op if the
        id is unknown — e.g. a shortcut left over from a session we have since
        re-bound)."""
        combo = self._ids.get(shortcut_id)
        if combo is None:
            return
        binding = self._bindings.get(combo)
        if binding is not None:
            binding.callback()

    # --- live portal seam (needs a real Wayland session) ------------------

    def _activate(self, specs: list[tuple[str, str, str, str]]) -> None:
        """Open a GlobalShortcuts session, bind ``specs``, and subscribe to
        ``Activated``. The only part that needs a live portal, so it is the seam
        tests replace; everything that decides *what* to bind and *how* an
        activation routes back is plain logic above.

        ``specs`` is ``(shortcut_id, combo, preferred_trigger, description)``.
        """
        from PySide6.QtDBus import QDBusConnection, QDBusInterface

        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            raise HotkeyUnavailable("no D-Bus session bus for the GlobalShortcuts portal")
        iface = QDBusInterface(_PORTAL_SERVICE, _PORTAL_PATH, _GLOBALSHORTCUTS_IFACE, bus)
        if not iface.isValid():
            raise HotkeyUnavailable(
                "the GlobalShortcuts portal is unavailable; install xdg-desktop-portal "
                "and a compositor backend that implements it, or bind a compositor "
                "shortcut to `squill capture`"
            )

        # Re-bind cleanly: the portal pins one shortcut set per session.
        self._deactivate()
        session = self._open_session(bus, iface)

        # Subscribe before binding so an immediate activation can't be missed.
        # A failed connect leaves a bound session whose activations never reach
        # us — a silently dead hotkey — so surface it instead of swallowing it.
        if not bus.connect(
            _PORTAL_SERVICE,
            _PORTAL_PATH,
            _GLOBALSHORTCUTS_IFACE,
            "Activated",
            self._on_activated,
        ):
            raise HotkeyUnavailable("could not subscribe to the GlobalShortcuts Activated signal")
        self._session_handle = session
        try:
            self._bind_shortcuts(bus, iface, session, specs)
        except Exception:
            self._deactivate()
            raise

    def _deactivate(self) -> None:
        """Disconnect the ``Activated`` subscription and close the portal session,
        if any. Safe to call when nothing is open (``stop`` and the re-bind path
        both lean on that)."""
        if self._session_handle is None:
            return
        from PySide6.QtDBus import QDBusConnection, QDBusInterface

        bus = QDBusConnection.sessionBus()
        bus.disconnect(
            _PORTAL_SERVICE,
            _PORTAL_PATH,
            _GLOBALSHORTCUTS_IFACE,
            "Activated",
            self._on_activated,
        )
        # Close the session object so the compositor drops our shortcuts. Use a
        # fire-and-forget asyncCall: teardown runs on every re-bind and at app
        # shutdown, and must not block on a slow or hung portal waiting for the
        # Close reply (we have nothing to do with it anyway).
        session = QDBusInterface(_PORTAL_SERVICE, self._session_handle, _SESSION_IFACE, bus)
        if session.isValid():
            session.asyncCall("Close")
        self._session_handle = None

    def _open_session(self, bus, iface) -> str:
        """``CreateSession`` round-trip → the session handle path."""
        token = "shotquill_" + uuid.uuid4().hex
        session_token = "shotquill_" + uuid.uuid4().hex
        options = {"handle_token": token, "session_handle_token": session_token}
        results = self._await_request(bus, iface.call("CreateSession", options))
        handle = results.get("session_handle")
        if not handle:
            raise HotkeyUnavailable("the GlobalShortcuts portal opened no session")
        return str(handle)

    def _bind_shortcuts(self, bus, iface, session: str, specs) -> None:
        """``BindShortcuts`` round-trip; the compositor may prompt the user once."""
        # a(sa{sv}): each shortcut is (id, {description, preferred_trigger}). The
        # portal is introspectable, so QtDBus marshals these native structures
        # against the published signature — exercised by the real-Wayland smoke.
        shortcuts = [
            [shortcut_id, {"description": description, "preferred_trigger": trigger}]
            for shortcut_id, _combo, trigger, description in specs
        ]
        self._await_request(bus, iface.call("BindShortcuts", session, shortcuts, "", {}))

    def _await_request(self, bus, reply) -> dict:
        """Drive a Qt event loop until a portal ``Request`` answers; return its
        results vardict (response code 0), or raise :class:`HotkeyUnavailable` on
        a rejected call, a cancel, or a timeout."""
        from PySide6.QtCore import QEventLoop, QTimer

        args = reply.arguments()
        if not args:
            raise HotkeyUnavailable(
                f"the GlobalShortcuts portal rejected a call: {reply.errorMessage()}"
            )
        request_path = args[0]

        state: dict = {}
        loop = QEventLoop()

        def _on_response(message) -> None:
            payload = message.arguments()
            state["response"] = payload[0] if payload else 2
            state["results"] = payload[1] if len(payload) > 1 else {}
            loop.quit()

        bus.connect(_PORTAL_SERVICE, request_path, _REQUEST_IFACE, "Response", _on_response)
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(_PORTAL_TIMEOUT_MS)
        loop.exec()
        bus.disconnect(_PORTAL_SERVICE, request_path, _REQUEST_IFACE, "Response", _on_response)

        if "response" not in state:
            raise HotkeyUnavailable("the GlobalShortcuts portal did not respond in time")
        if state["response"] != 0:
            raise HotkeyUnavailable("the GlobalShortcuts request was cancelled")
        return state.get("results") or {}

    def _on_activated(self, message) -> None:
        """QtDBus ``Activated(o session, s id, t time, a{sv} opts)`` handler:
        route the shortcut id back to its callback, but only for our own *open*
        session. When the session handle does not match — including after
        ``stop()`` left it ``None`` — reject the signal, so a late or stray
        ``Activated`` (a disconnect race, or the portal signalling a session we
        just closed) can never fire a capture once we have torn down."""
        args = message.arguments()
        if len(args) < 2:
            return
        session_handle, shortcut_id = str(args[0]), str(args[1])
        if self._session_handle is None or session_handle != self._session_handle:
            return
        self._dispatch(shortcut_id)


def globalshortcuts_available() -> bool:
    """Best-effort: is the GlobalShortcuts portal reachable on the session bus?

    ``squill doctor`` and the app's hotkey setup use this to tell a Wayland box
    that simply lacks the interface (older xdg-desktop-portal, or a compositor
    with no GlobalShortcuts backend) from a transient failure. Any error — no
    session bus, no portal — reads as unavailable rather than raising."""
    try:
        from PySide6.QtDBus import QDBusConnection, QDBusInterface

        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return False
        iface = QDBusInterface(_PORTAL_SERVICE, _PORTAL_PATH, _GLOBALSHORTCUTS_IFACE, bus)
        return bool(iface.isValid())
    except Exception:
        return False
