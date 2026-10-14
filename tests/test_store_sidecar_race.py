"""SQLite sidecars can disappear between the existence check and lstat()."""

from __future__ import annotations

import errno
from pathlib import Path

import pytest


@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_sidecar_removed_after_existence_check_is_treated_as_absent(ctx, monkeypatch, suffix):
    sidecar = Path(str(ctx.paths.database) + suffix)
    real_exists, real_lstat = Path.exists, Path.lstat

    def seen(self, *args, **kwargs):
        return True if self == sidecar else real_exists(self, *args, **kwargs)

    def vanished(self, *args, **kwargs):
        # Another connection closed and SQLite removed the sidecar after exists() saw it.
        if self == sidecar:
            raise FileNotFoundError(errno.ENOENT, "No such file or directory")
        return real_lstat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", seen)
    monkeypatch.setattr(Path, "lstat", vanished)
    with ctx.store.connection() as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
