# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
import pytest

from shotquill.hotkeys.combo import parse_combo, to_pynput_combo


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
