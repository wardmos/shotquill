# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""macOS global hotkeys via Carbon ``RegisterEventHotKey``.

This backend registers each configured key combination with the system instead
of listening to every keyboard event. That keeps Option-based shortcuts reliable
by matching hardware virtual key codes, avoids the Input Monitoring permission
required by raw event taps, and lets macOS consume the registered shortcut before
it reaches the foreground application.
"""

from __future__ import annotations

import ctypes
import ctypes.util
from collections.abc import Callable
from dataclasses import dataclass

from shotquill.hotkeys.base import HotkeyManager, HotkeyUnavailable
from shotquill.hotkeys.combo import parse_combo

# macOS hardware virtual key codes (kVK_ANSI_* / kVK_F*), keyed by the lowercase
# token the settings UI emits (a-z, 0-9, f1-f12).
_MAC_VK: dict[str, int] = {
    # letters
    "a": 0,
    "s": 1,
    "d": 2,
    "f": 3,
    "h": 4,
    "g": 5,
    "z": 6,
    "x": 7,
    "c": 8,
    "v": 9,
    "b": 11,
    "q": 12,
    "w": 13,
    "e": 14,
    "r": 15,
    "y": 16,
    "t": 17,
    "o": 31,
    "u": 32,
    "i": 34,
    "p": 35,
    "l": 37,
    "j": 38,
    "k": 40,
    "n": 45,
    "m": 46,
    # number row
    "1": 18,
    "2": 19,
    "3": 20,
    "4": 21,
    "5": 23,
    "6": 22,
    "7": 26,
    "8": 28,
    "9": 25,
    "0": 29,
    # function keys
    "f1": 122,
    "f2": 120,
    "f3": 99,
    "f4": 118,
    "f5": 96,
    "f6": 97,
    "f7": 98,
    "f8": 100,
    "f9": 101,
    "f10": 109,
    "f11": 103,
    "f12": 111,
}

# Carbon modifier masks from Events.h.
_CARBON_MODS = {
    "cmd": 1 << 8,
    "shift": 1 << 9,
    "alt": 1 << 11,
    "ctrl": 1 << 12,
}

_NO_ERR = 0
_EVENT_HOTKEY_PRESSED = 5
_EVENT_CLASS_KEYBOARD = int.from_bytes(b"keyb", "big")
_EVENT_PARAM_DIRECT_OBJECT = int.from_bytes(b"----", "big")
_TYPE_EVENT_HOTKEY_ID = int.from_bytes(b"hkid", "big")
_SIGNATURE = int.from_bytes(b"SQuL", "big")


class _EventTypeSpec(ctypes.Structure):
    _fields_ = [
        ("eventClass", ctypes.c_uint32),
        ("eventKind", ctypes.c_uint32),
    ]


class _EventHotKeyID(ctypes.Structure):
    _fields_ = [
        ("signature", ctypes.c_uint32),
        ("id", ctypes.c_uint32),
    ]


_EventHandlerProc = ctypes.CFUNCTYPE(
    ctypes.c_int32,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
)


@dataclass(frozen=True)
class _Binding:
    combo: str
    mods: int
    vk: int
    callback: Callable[[], None]


@dataclass
class _RegisteredBinding:
    binding: _Binding
    ref: ctypes.c_void_p


@dataclass
class _CarbonAPI:
    register_hotkey: Callable
    unregister_hotkey: Callable
    install_event_handler: Callable
    remove_event_handler: Callable
    get_application_event_target: Callable
    get_event_parameter: Callable


def _load_carbon_api() -> _CarbonAPI:
    path = (
        ctypes.util.find_library("Carbon")
        or "/System/Library/Frameworks/Carbon.framework/Carbon"
    )
    try:
        carbon = ctypes.CDLL(path)
    except OSError as exc:
        raise HotkeyUnavailable("macOS Carbon hotkey API is unavailable") from exc

    carbon.RegisterEventHotKey.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        _EventHotKeyID,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    carbon.RegisterEventHotKey.restype = ctypes.c_int32
    carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
    carbon.UnregisterEventHotKey.restype = ctypes.c_int32
    carbon.InstallEventHandler.argtypes = [
        ctypes.c_void_p,
        _EventHandlerProc,
        ctypes.c_uint32,
        ctypes.POINTER(_EventTypeSpec),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    carbon.InstallEventHandler.restype = ctypes.c_int32
    carbon.RemoveEventHandler.argtypes = [ctypes.c_void_p]
    carbon.RemoveEventHandler.restype = ctypes.c_int32
    carbon.GetApplicationEventTarget.argtypes = []
    carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
    carbon.GetEventParameter.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    carbon.GetEventParameter.restype = ctypes.c_int32

    return _CarbonAPI(
        register_hotkey=carbon.RegisterEventHotKey,
        unregister_hotkey=carbon.UnregisterEventHotKey,
        install_event_handler=carbon.InstallEventHandler,
        remove_event_handler=carbon.RemoveEventHandler,
        get_application_event_target=carbon.GetApplicationEventTarget,
        get_event_parameter=carbon.GetEventParameter,
    )


class MacHotkeyManager(HotkeyManager):
    def __init__(self) -> None:
        self._bindings: dict[str, Callable[[], None]] = {}
        self._compiled: dict[str, _Binding] = {}
        self._registered: dict[int, _RegisteredBinding] = {}
        self._api: _CarbonAPI | None = None
        self._handler_ref: ctypes.c_void_p | None = None
        self._handler_proc: _EventHandlerProc | None = None
        self._next_id = 1

    def register(
        self, combo: str, callback: Callable[[], None], description: str | None = None
    ) -> None:
        # ``description`` is meaningful to the Wayland portal backend; Carbon's
        # global hotkey API registers only the key combination.
        self._bindings[combo] = callback

    def unregister(self, combo: str) -> None:
        self._bindings.pop(combo, None)
        binding = self._compiled.pop(combo, None)
        if binding is not None:
            self._unregister_binding(binding)

    def clear(self) -> None:
        self._bindings.clear()
        self._compiled.clear()
        self._unregister_all()

    def start(self) -> None:
        self._unregister_all()
        self._compiled = {
            combo: self._compile(combo, callback) for combo, callback in self._bindings.items()
        }
        if not self._compiled:
            return
        self._ensure_api()
        self._ensure_handler()
        try:
            for binding in self._compiled.values():
                self._register_binding(binding)
        except Exception:
            self._unregister_all()
            raise

    def stop(self) -> None:
        self._unregister_all()
        if self._handler_ref is not None and self._api is not None:
            self._api.remove_event_handler(self._handler_ref)
            self._handler_ref = None
        self._handler_proc = None

    @staticmethod
    def _compile(combo: str, callback: Callable[[], None]) -> _Binding:
        parsed = parse_combo(combo)
        key = str(parsed["key"]).lower()
        if key not in _MAC_VK:
            raise HotkeyUnavailable(f"unsupported macOS hotkey key: {key!r}")
        mods = 0
        for name, mask in _CARBON_MODS.items():
            if parsed[name]:
                mods |= mask
        return _Binding(combo=combo, mods=mods, vk=_MAC_VK[key], callback=callback)

    def _ensure_api(self) -> None:
        if self._api is None:
            self._api = _load_carbon_api()

    def _ensure_handler(self) -> None:
        if self._handler_ref is not None:
            return
        assert self._api is not None
        target = self._api.get_application_event_target()
        if not target:
            raise HotkeyUnavailable("macOS application event target is unavailable")
        event_type = _EventTypeSpec(_EVENT_CLASS_KEYBOARD, _EVENT_HOTKEY_PRESSED)
        handler_ref = ctypes.c_void_p()
        self._handler_proc = _EventHandlerProc(self._handle_event)
        status = self._api.install_event_handler(
            target,
            self._handler_proc,
            1,
            ctypes.byref(event_type),
            None,
            ctypes.byref(handler_ref),
        )
        if status != _NO_ERR:
            self._handler_proc = None
            raise HotkeyUnavailable(f"macOS hotkey event handler failed ({status})")
        self._handler_ref = handler_ref

    def _register_binding(self, binding: _Binding) -> None:
        assert self._api is not None
        hotkey_id = self._next_id
        self._next_id += 1
        carbon_id = _EventHotKeyID(_SIGNATURE, hotkey_id)
        ref = ctypes.c_void_p()
        status = self._api.register_hotkey(
            binding.vk,
            binding.mods,
            carbon_id,
            self._api.get_application_event_target(),
            0,
            ctypes.byref(ref),
        )
        if status != _NO_ERR:
            raise HotkeyUnavailable(
                f"macOS hotkey registration failed for {binding.combo!r} ({status})"
            )
        self._registered[hotkey_id] = _RegisteredBinding(binding=binding, ref=ref)

    def _unregister_binding(self, binding: _Binding) -> None:
        for hotkey_id, registered in list(self._registered.items()):
            if registered.binding.combo == binding.combo:
                self._unregister_hotkey_ref(registered.ref)
                self._registered.pop(hotkey_id, None)

    def _unregister_all(self) -> None:
        for registered in list(self._registered.values()):
            self._unregister_hotkey_ref(registered.ref)
        self._registered.clear()

    def _unregister_hotkey_ref(self, ref: ctypes.c_void_p) -> None:
        if self._api is not None:
            self._api.unregister_hotkey(ref)

    def _handle_event(self, _next_handler, event, _user_data) -> int:
        assert self._api is not None
        hotkey_id = _EventHotKeyID()
        status = self._api.get_event_parameter(
            event,
            _EVENT_PARAM_DIRECT_OBJECT,
            _TYPE_EVENT_HOTKEY_ID,
            None,
            ctypes.sizeof(_EventHotKeyID),
            None,
            ctypes.byref(hotkey_id),
        )
        if status != _NO_ERR:
            return status
        if hotkey_id.signature != _SIGNATURE:
            return _NO_ERR
        registered = self._registered.get(hotkey_id.id)
        if registered is not None:
            registered.binding.callback()
        return _NO_ERR
