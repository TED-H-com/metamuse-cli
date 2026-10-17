"""A recheck can observe a second unlinked inode; bounded checks remain strict."""

import os
from pathlib import Path

import pytest

from metamuse import models as m
from metamuse.store import _regular_file


@pytest.mark.parametrize(
    "replacement", ["regular", "absent", "symlink", "hardlink", "directory", "exhausted"]
)
def test_repeated_unlinked_observations_recheck_and_refuse_unsafe_replacements(
    tmp_path,
    monkeypatch,
    replacement,
):
    path = tmp_path / "state.sqlite3-wal"
    path.write_bytes(b"synthetic sidecar")
    unlinked = tmp_path / "unlinked-inode"
    unlinked.write_bytes(b"synthetic retired sidecar")
    target = tmp_path / "target"
    target.write_bytes(b"must not be changed")
    fd = os.open(unlinked, os.O_PATH | os.O_NOFOLLOW)
    unlinked.unlink()
    zero = os.fstat(fd)
    os.close(fd)
    assert zero.st_nlink == 0  # Real kernel stat, without forged mode/link fields.
    original = Path.lstat
    calls, observations = 0, 0

    def racing(self, *args, **kwargs):
        nonlocal calls, observations
        if self != path:
            return original(self, *args, **kwargs)
        calls += 1
        if calls % 2 or (observations >= 2 and replacement != "exhausted"):
            return original(self, *args, **kwargs)
        observations += 1
        if observations == 2 and replacement not in ("regular", "exhausted"):
            path.unlink()
            if replacement == "symlink":
                path.symlink_to(target)
            elif replacement == "hardlink":
                os.link(target, path)
            elif replacement == "directory":
                path.mkdir()
        return zero

    monkeypatch.setattr(Path, "lstat", racing)
    if replacement == "regular":
        _regular_file(path, _recheck_unlinked=True)
    elif replacement == "absent":
        with pytest.raises(FileNotFoundError):
            _regular_file(path, _recheck_unlinked=True)
    else:
        with pytest.raises(m.CoreError) as error:
            _regular_file(path, _recheck_unlinked=True)
        assert error.value.detail.code == m.ErrorCode.RESOURCE_UNAVAILABLE
        if replacement == "exhausted":
            assert "changed repeatedly" in error.value.detail.message
            assert observations == 4
    assert observations >= 2
    assert target.read_bytes() == b"must not be changed"
