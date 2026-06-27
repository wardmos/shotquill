# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Tests for unified opt-in debug logging."""

from __future__ import annotations

import io
import logging

from shotquill import debug_log, paths
from shotquill.ui import _debug


class _Config:
    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled

    def debug_mode(self) -> bool:
        return self._enabled


def _flush_debug_handlers() -> None:
    for handler in debug_log._debug_handlers(debug_log.get_logger()):
        handler.flush()


def test_disabled_by_default_does_not_create_log(monkeypatch, tmp_path):
    monkeypatch.delenv(debug_log.ENV_DEBUG, raising=False)
    monkeypatch.setattr(debug_log, "DEFAULT_DEBUG_MODE", False)
    monkeypatch.setattr(paths, "debug_log_path", lambda: tmp_path / "debug.log")

    assert debug_log.configure() is None
    debug_log.get_logger("test").debug("ignored")

    assert not (tmp_path / "debug.log").exists()
    assert debug_log._null_handlers(debug_log.get_logger())


def test_disabled_exception_logging_does_not_use_last_resort(monkeypatch, tmp_path):
    monkeypatch.delenv(debug_log.ENV_DEBUG, raising=False)
    monkeypatch.setattr(debug_log, "DEFAULT_DEBUG_MODE", False)
    monkeypatch.setattr(paths, "debug_log_path", lambda: tmp_path / "debug.log")
    stream = io.StringIO()
    monkeypatch.setattr(logging, "lastResort", logging.StreamHandler(stream))

    assert debug_log.configure() is None
    try:
        raise RuntimeError("hidden")
    except RuntimeError:
        debug_log.get_logger("test").exception("suppressed")

    assert stream.getvalue() == ""


def test_config_enables_file_logging(monkeypatch, tmp_path):
    log = tmp_path / "debug.log"
    monkeypatch.delenv(debug_log.ENV_DEBUG, raising=False)
    monkeypatch.setattr(paths, "debug_log_path", lambda: log)

    assert debug_log.configure(_Config(True)) == log
    debug_log.get_logger("test").debug("hello %s", "debug")
    _flush_debug_handlers()

    text = log.read_text(encoding="utf-8")
    assert "hello debug" in text
    assert "shotquill.test" in text


def test_env_enables_logging_without_config(monkeypatch, tmp_path):
    log = tmp_path / "debug.log"
    monkeypatch.setenv(debug_log.ENV_DEBUG, "1")
    monkeypatch.setattr(paths, "debug_log_path", lambda: log)

    assert debug_log.configure() == log
    debug_log.get_logger("env").debug("from env")
    _flush_debug_handlers()

    assert "from env" in log.read_text(encoding="utf-8")


def test_env_false_overrides_enabled_config(monkeypatch, tmp_path):
    monkeypatch.setenv(debug_log.ENV_DEBUG, "0")
    monkeypatch.setattr(paths, "debug_log_path", lambda: tmp_path / "debug.log")

    assert debug_log.is_enabled(_Config(True)) is False
    assert debug_log.configure(_Config(True)) is None


def test_configure_swallows_log_path_errors(monkeypatch):
    monkeypatch.delenv(debug_log.ENV_DEBUG, raising=False)

    def _boom():
        raise OSError("log path denied")

    monkeypatch.setattr(paths, "debug_log_path", _boom)

    assert debug_log.configure(_Config(True)) is None
    assert debug_log._null_handlers(debug_log.get_logger())


def test_new_operation_id_is_prefixed_and_unique():
    first = debug_log.new_operation_id("Capture")
    second = debug_log.new_operation_id("Capture")
    assert first.startswith("capture-")
    assert second.startswith("capture-")
    assert first != second


def test_reconfigure_disables_existing_handler(monkeypatch, tmp_path):
    log = tmp_path / "debug.log"
    monkeypatch.delenv(debug_log.ENV_DEBUG, raising=False)
    monkeypatch.setattr(paths, "debug_log_path", lambda: log)

    debug_log.configure(_Config(True))
    assert debug_log._debug_handlers(debug_log.get_logger())

    debug_log.configure(_Config(False))

    assert debug_log._debug_handlers(debug_log.get_logger()) == []


def test_repeated_configure_reuses_one_handler(monkeypatch, tmp_path):
    log = tmp_path / "debug.log"
    monkeypatch.delenv(debug_log.ENV_DEBUG, raising=False)
    monkeypatch.setattr(paths, "debug_log_path", lambda: log)

    debug_log.configure(_Config(True))
    debug_log.configure(_Config(True))

    assert len(debug_log._debug_handlers(debug_log.get_logger())) == 1


def test_crop_log_uses_unified_debug_log(monkeypatch, tmp_path):
    log = tmp_path / "debug.log"
    monkeypatch.delenv(debug_log.ENV_DEBUG, raising=False)
    monkeypatch.setattr(paths, "debug_log_path", lambda: log)

    debug_log.configure(_Config(True))
    _debug.crop_log("entry one")
    _flush_debug_handlers()

    assert "crop entry one" in log.read_text(encoding="utf-8")


def test_crop_log_swallows_logger_errors(monkeypatch):
    class _Boom:
        def debug(self, *args, **kwargs):
            raise OSError("log denied")

    monkeypatch.setattr(_debug, "_LOG", _Boom())
    _debug.crop_log("entry")


def teardown_module(module):
    debug_log._remove_debug_handlers(logging.getLogger("shotquill"))
    debug_log._remove_null_handlers(logging.getLogger("shotquill"))
