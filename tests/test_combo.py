# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
import pytest

from shotquill.hotkeys.combo import (
    parse_combo,
    portal_shortcut_id,
    to_portal_trigger,
    to_pynput_combo,
)


def test_to_combo_default_hotkeys():
    assert to_pynput_combo(alt=True, key="a") == "<alt>+a"
    assert to_pynput_combo(alt=True, key="s") == "<alt>+s"


def test_to_combo_orders_modifiers_and_lowercases_key():
    assert to_pynput_combo(cmd=True, shift=True, key="A") == "<cmd>+<shift>+a"


def test_to_combo_requires_key():
    with pytest.raises(ValueError):
        to_pynput_combo(alt=True, key="")


def test_parse_combo():
    assert parse_combo("<alt>+a") == {
        "cmd": False,
        "ctrl": False,
        "alt": True,
        "shift": False,
        "key": "a",
    }


@pytest.mark.parametrize("combo", ["<alt>+a", "<cmd>+<shift>+s", "<ctrl>+<alt>+1"])
def test_round_trip(combo):
    parsed = parse_combo(combo)
    rebuilt = to_pynput_combo(
        cmd=parsed["cmd"],
        ctrl=parsed["ctrl"],
        alt=parsed["alt"],
        shift=parsed["shift"],
        key=parsed["key"],
    )
    assert rebuilt == combo


def test_to_portal_trigger_maps_modifiers_and_super():
    # cmd is the Super/Meta key, which the portal grammar spells LOGO; modifiers
    # are upper-cased and ordered, the character key stays as-is.
    assert to_portal_trigger("<cmd>+<shift>+a") == "LOGO+SHIFT+a"
    assert to_portal_trigger("<ctrl>+<alt>+1") == "CTRL+ALT+1"
    assert to_portal_trigger("<alt>+a") == "ALT+a"


def test_to_portal_trigger_uppercases_function_keys():
    assert to_portal_trigger("<ctrl>+f5") == "CTRL+F5"


def test_to_portal_trigger_requires_key():
    with pytest.raises(ValueError):
        to_portal_trigger("<ctrl>+<shift>")


def test_portal_shortcut_id_is_stable_and_safe():
    # Deterministic (same combo -> same id) and restricted to a portal-safe
    # character set, so no compositor chokes on the identifier.
    assert portal_shortcut_id("<cmd>+<shift>+a") == "sq_cmd_shift_a"
    assert portal_shortcut_id("<cmd>+<shift>+a") == portal_shortcut_id("<cmd>+<shift>+a")
    assert all(c.isalnum() or c == "_" for c in portal_shortcut_id("<ctrl>+<alt>+1"))
