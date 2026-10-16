"""Closing SQLite can unlink an inode while a sidecar stat is in progress."""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

import pytest

from metamuse import models as m
from metamuse import store


@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
@pytest.mark.parametrize("replacement", ["absent", "symlink", "hardlink", "directory"])
def test_last_close_during_sidecar_stat_rechecks_current_path(
    ctx, tmp_path, monkeypatch, suffix, replacement
):
    sidecar = Path(str(ctx.paths.database) + suffix)
    ready, close_requested, closed = (threading.Event() for _ in range(3))
    errors = []

    def owner():
        conn = None
        try:
            conn = sqlite3.connect(ctx.paths.database)
            conn.execute("SELECT count(*) FROM sqlite_schema").fetchone()
            ready.set()
            if not close_requested.wait(10):
                raise TimeoutError("Synthetic sidecar close was not requested")
        except BaseException as exc:
            errors.append(exc)
        finally:
            if conn is not None:
                conn.close()
            closed.set()

    thread = threading.Thread(target=owner)
    regular_file, lstat = store._regular_file, Path.lstat
    armed = True
    observed = []
    target = tmp_path / "private-target"
    target.write_bytes(b"synthetic private bytes")
    target.chmod(0o600)

    def checked(path, *args, **kwargs):
        nonlocal armed
        if path != sidecar or not armed:
            return regular_file(path, *args, **kwargs)
        armed = False
        calls = 0

        def pinned_stat(self, *args, **kwargs):
            nonlocal calls
            if self != sidecar:
                return lstat(self, *args, **kwargs)
            calls += 1
            if calls != 2:
                return lstat(self, *args, **kwargs)
            # Model the lookup/getattr split in a racing lstat with a real pinned
            # inode. SQLite itself removes the sidecar; no stat fields are forged.
            fd = os.open(self, os.O_PATH | os.O_NOFOLLOW)
            try:
                close_requested.set()
                assert closed.wait(10)
                assert not errors
                info = os.fstat(fd)
                assert info.st_nlink == 0
                observed.append(info)
                assert not sidecar.exists()
                if replacement == "symlink":
                    sidecar.symlink_to(target)
                elif replacement == "hardlink":
                    os.link(target, sidecar)
                elif replacement == "directory":
                    sidecar.mkdir()
                return info
            finally:
                os.close(fd)

        with monkeypatch.context() as patch:
            patch.setattr(Path, "lstat", pinned_stat)
            return regular_file(path, *args, **kwargs)

    try:
        thread.start()
        assert ready.wait(10)
        assert not errors
        assert sidecar.is_file()
        monkeypatch.setattr(store, "_regular_file", checked)
        if replacement == "absent":
            with ctx.store.connection() as conn:
                assert conn.execute("SELECT 1").fetchone()[0] == 1
        else:
            with pytest.raises(m.CoreError) as caught, ctx.store.connection():
                pytest.fail("Unsafe replacement reached an open connection")
            assert caught.value.detail.code == m.ErrorCode.RESOURCE_UNAVAILABLE
        assert len(observed) == 1
        assert target.read_bytes() == b"synthetic private bytes"
        assert target.stat().st_mode & 0o777 == 0o600
    finally:
        close_requested.set()
        if thread.ident is not None:
            thread.join(10)
    assert not thread.is_alive()
    assert not errors


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
@pytest.mark.parametrize("role", ["database", "archive"])
def test_database_and_archive_links_remain_refused(ctx, tmp_path, kind, role):
    target = tmp_path / "original"
    target.write_bytes(b"synthetic private archive")
    target.chmod(0o600)
    path = ctx.paths.database if role == "database" else tmp_path / "archive.sqlite3"
    if role == "database":
        path.unlink()
    if kind == "symlink":
        path.symlink_to(target)
    else:
        os.link(target, path)
    with pytest.raises(m.CoreError) as caught:
        if role == "database":
            with ctx.store.connection():
                pytest.fail("Linked database reached an open connection")
        else:
            store._regular_file(path)
    assert caught.value.detail.code == m.ErrorCode.RESOURCE_UNAVAILABLE
    assert target.read_bytes() == b"synthetic private archive"
    assert target.stat().st_mode & 0o777 == 0o600
