"""Unreleased v5 accounts mirror observation hash and VM normalization CHECKs."""

import pytest
from test_muse_trust import freeze

from metamuse.models import CoreError
from metamuse.paths import WorkspacePaths
from metamuse.store import MIGRATIONS, Store


@pytest.mark.parametrize("version", [4, 5])
@pytest.mark.parametrize(
    "field,value",
    [
        ("viewer_hash", "A" * 64),
        ("session_hash", "g" * 64),
        ("active_vm_id", ""),
        ("active_vm_id", " VM1 "),
        ("active_vm_id", "VM1"),
        ("active_vm_id", "bad/id"),
    ],
)
def test_r4_11_account_checks_fresh_and_upgrade(tmp_path, version, field, value):
    paths = WorkspacePaths.resolve(tmp_path / "ws")
    if version == 4:
        Store(paths, schema_version=4, migrations={n: MIGRATIONS[n] for n in range(1, 5)})
    store = Store(paths)
    row = dict(
        provider="synthetic",
        account="a",
        version=1,
        viewer_hash="a" * 64,
        session_hash="b" * 64,
        active_vm_id="vm1",
        generation=1,
        confidential_required=0,
        updated_at="t",
    )
    row[field] = value
    with pytest.raises(CoreError):
        with store.transaction() as conn:
            conn.execute(
                "INSERT INTO muse_trust_accounts VALUES (?,?,?,?,?,?,?,?,?)", tuple(row.values())
            )
    snapshot = freeze(store)
    with pytest.raises(CoreError):
        with store.transaction() as conn:
            conn.execute(
                f"UPDATE muse_trust_accounts SET {field}=?,generation=generation+1", (value,)
            )
    store.muse_assert_current(snapshot)
    with store.connection() as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_r4_11_account_checks_survive_backup_restore(ctx, tmp_path):
    freeze(ctx.store, confidential=True)
    archive = tmp_path / "backup"
    ctx.store.backup(archive)
    # Exercise restore using the established public Store API.
    paths = WorkspacePaths.resolve(tmp_path / "restored")
    Store.restore(archive, paths)
    restored = Store(paths)
    assert freeze(restored).confidential_required
    with pytest.raises(CoreError):
        with restored.transaction() as conn:
            conn.execute(
                "INSERT INTO muse_trust_accounts VALUES ('x','x',1,?,?,'',1,1,'t')",
                ("c" * 64, "d" * 64),
            )
