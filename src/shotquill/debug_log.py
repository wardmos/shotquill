# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Unified opt-in debug logging.

Debug mode has one public decision point: :func:`is_enabled`. The value can be
turned on persistently through config, forced for a launch with
``SHOTQUILL_DEBUG``, or changed by editing ``DEFAULT_DEBUG_MODE`` for a build.
When enabled, all ``shotquill.*`` loggers write debug lines to the platform log
directory returned by :func:`shotquill.paths.debug_log_path`.
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Protocol

from shotquill import paths

DEFAULT_DEBUG_MODE = False
ENV_DEBUG = "SHOTQUILL_DEBUG"
_LOGGER_NAME = "shotquill"
_HANDLER_MARKER = "_shotquill_debug_handler"
_NULL_HANDLER_MARKER = "_shotquill_null_handler"


class _DebugConfig(Protocol):
    def debug_mode(self) -> bool: ...


def _env_override() -> bool | None:
    raw = os.environ.get(ENV_DEBUG)
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in ("", "0", "false", "no", "off"):
        return False
    return True


def is_enabled(config: _DebugConfig | None = None) -> bool:
    """Return the unified debug-mode state.

    An explicit environment value wins for one launch. Without it, a provided
    config object supplies the persisted switch. If neither is present, the
    hardcoded build default is used.
    """
    override = _env_override()
    if override is not None:
        return override
    if config is not None:
        try:
            return bool(config.debug_mode())
        except Exception:
            return DEFAULT_DEBUG_MODE
    return DEFAULT_DEBUG_MODE


def get_logger(name: str | None = None) -> logging.Logger:
    """Return the app logger or a child logger under ``shotquill``."""
    if name is None or name == _LOGGER_NAME:
        return logging.getLogger(_LOGGER_NAME)
    if name.startswith(_LOGGER_NAME + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_LOGGER_NAME}.{name}")


def new_operation_id(prefix: str) -> str:
    """Short correlation id for one user-visible operation."""
    safe_prefix = "".join(ch for ch in prefix.lower() if ch.isalnum() or ch == "-") or "op"
    return f"{safe_prefix}-{uuid.uuid4().hex[:8]}"


def configure(config: _DebugConfig | None = None) -> Path | None:
    """Configure file logging for debug mode; return the active path.

    This is safe to call repeatedly after Settings changes. It never raises:
    diagnostics must not block capture, CLI, or app startup.
    """
    logger = get_logger()
    if not is_enabled(config):
        _remove_debug_handlers(logger)
        _install_null_handler(logger)
        return None

    try:
        path = paths.debug_log_path()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _install_file_handler(logger, path)
    except Exception:
        _install_null_handler(logger)
        return None
    logger.debug(
        "debug logging enabled pid=%s program=%s path=%s",
        os.getpid(),
        Path(sys.argv[0]).name if sys.argv else "",
        path,
    )
    return path


def _install_file_handler(logger: logging.Logger, path: Path) -> None:
    _remove_null_handlers(logger)
    existing = _debug_handlers(logger)
    if existing and all(Path(handler.baseFilename) == path for handler in existing):
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        return
    _remove_debug_handlers(logger)
    handler = logging.FileHandler(path, encoding="utf-8")
    setattr(handler, _HANDLER_MARKER, True)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s pid=%(process)d tid=%(threadName)s %(message)s"
        )
    )
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False


def _debug_handlers(logger: logging.Logger) -> list[logging.FileHandler]:
    return [
        handler
        for handler in logger.handlers
        if getattr(handler, _HANDLER_MARKER, False) and isinstance(handler, logging.FileHandler)
    ]


def _null_handlers(logger: logging.Logger) -> list[logging.NullHandler]:
    return [
        handler
        for handler in logger.handlers
        if getattr(handler, _NULL_HANDLER_MARKER, False)
        and isinstance(handler, logging.NullHandler)
    ]


def _install_null_handler(logger: logging.Logger) -> None:
    if not _null_handlers(logger):
        handler = logging.NullHandler()
        setattr(handler, _NULL_HANDLER_MARKER, True)
        logger.addHandler(handler)
    logger.setLevel(logging.NOTSET)
    logger.propagate = False


def _remove_debug_handlers(logger: logging.Logger) -> None:
    for handler in _debug_handlers(logger):
        logger.removeHandler(handler)
        handler.close()


def _remove_null_handlers(logger: logging.Logger) -> None:
    for handler in _null_handlers(logger):
        logger.removeHandler(handler)
        handler.close()
