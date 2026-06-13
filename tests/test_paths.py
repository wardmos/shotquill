# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 wardmos
"""Platform directory seams: headless capture temp dir and audit log location."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from shotquill import paths


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX file-mode semantics")
def test_capture_tmp_dir_is_private_and_under_tempdir(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))

    directory = paths.capture_tmp_dir()

    assert directory == tmp_path / "shotquill"
    assert directory.is_dir()
    # The temp root is world-shared and screenshots are sensitive: owner-only.
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_capture_tmp_dir_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    first = paths.capture_tmp_dir()
    # A second call must not raise on the existing directory.
    assert paths.capture_tmp_dir() == first


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX file-mode semantics")
def test_capture_tmp_dir_retightens_drifted_permissions(tmp_path, monkeypatch):
    # mkdir's mode= only applies at creation; a directory that already exists
    # with looser permissions must be tightened back to owner-only.
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    existing = tmp_path / "shotquill"
    existing.mkdir()
    existing.chmod(0o755)

    directory = paths.capture_tmp_dir()

    assert stat.S_IMODE(directory.stat().st_mode) == 0o700


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX ownership check")
def test_capture_tmp_dir_refuses_foreign_owned_dir(tmp_path, monkeypatch):
    # The well-known name in the shared temp root can be squatted by another
    # user; captures are sensitive, so refuse rather than write into it.
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    (tmp_path / "shotquill").mkdir()
    other_uid = os.getuid() + 1
    monkeypatch.setattr(os, "getuid", lambda: other_uid)

    with pytest.raises(OSError, match="another user"):
        paths.capture_tmp_dir()


def test_capture_tmp_dir_refuses_symlink(tmp_path, monkeypatch):
    # A symlink squatted at the well-known name would redirect captures to an
    # attacker-chosen location; lstat sees the link itself, so it is refused.
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "shotquill").symlink_to(elsewhere)

    with pytest.raises(OSError, match="not a directory"):
        paths.capture_tmp_dir()


def test_audit_log_path_honors_xdg_state_home(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    log = paths.audit_log_path()

    assert log == tmp_path / "state" / "shotquill" / "audit.log"
    # The parent is created so callers can append without ceremony.
    assert log.parent.is_dir()


def test_audit_log_path_empty_xdg_falls_back_to_local_state(tmp_path, monkeypatch):
    # An *empty* XDG_STATE_HOME is "unset" per the XDG spec, not the CWD.
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("XDG_STATE_HOME", "")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    log = paths.audit_log_path()

    assert log == tmp_path / ".local" / "state" / "shotquill" / "audit.log"


def test_audit_log_path_macos_uses_library_logs(tmp_path, monkeypatch):
    # ~/Library/Logs keeps the audit trail visible in Console.app.
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # XDG must be ignored on macOS even when set.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))

    log = paths.audit_log_path()

    assert log == tmp_path / "Library" / "Logs" / "shotquill" / "audit.log"
    assert not (tmp_path / "xdg").exists()


def test_audit_log_path_unset_xdg_falls_back_to_local_state(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert paths.audit_log_path() == tmp_path / ".local" / "state" / "shotquill" / "audit.log"


def test_capture_tmp_dir_used_as_default_destination_exists_after_call(tmp_path, monkeypatch):
    # The contract callers (CLI/MCP) rely on: the returned dir already exists,
    # so a capture can be written into it immediately.
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path / "deep" / "nested"))
    directory = paths.capture_tmp_dir()
    probe = directory / "probe.png"
    probe.write_bytes(b"\x89PNG")
    assert probe.read_bytes() == b"\x89PNG"
    assert os.access(directory, os.W_OK)
