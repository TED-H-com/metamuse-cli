"""Every SQLite primary class has fixed, honest guidance on all trust paths."""

import errno
import sqlite3
from contextlib import contextmanager
import pytest
from test_muse_standard import Connecting, assignment, response, rv
from test_muse_transport import FakeHttp, build, ok_frames
from test_muse_trust import freeze
from metamuse.models import CoreError, ErrorCode

ENV = ["IOERR", "CANTOPEN", "PERM", "NOLFS", "READONLY", "FULL", "IOERR_FSYNC", "READONLY_DBMOVED"]
RETRY = ["BUSY", "LOCKED", "BUSY_SNAPSHOT", "PROTOCOL", "NOMEM", "INTERRUPT"]
DAMAGE = ["CORRUPT", "NOTADB", "SCHEMA", "CONSTRAINT", "CORRUPT_INDEX"]
INTERNAL = [
    "ERROR",
    "MISMATCH",
    "TOOBIG",
    "RANGE",
    "FORMAT",
    "INTERNAL",
    "MISUSE",
    "ABORT",
    "EMPTY",
    "NOTFOUND",
    "NOTICE",
    "WARNING",
    "AUTH",
    "python",
]


@pytest.mark.parametrize("name", ENV + RETRY + DAMAGE + INTERNAL + ["ENOSPC", "EROFS"])
@pytest.mark.parametrize("path", ["freeze", "transport", "hint", "rv", "label_busy"])
def test_r5_7_fixed_storage_classes(ctx, monkeypatch, name, path):
    original = ctx.store.transaction
    failing = {"on": path not in {"rv", "label_busy"}}

    @contextmanager
    def transaction():
        if failing["on"]:
            if name == "python":
                raise TypeError("private SQL sentinel")
            if name in {"ENOSPC", "EROFS"}:
                raise OSError(getattr(errno, name), "private SQL sentinel")
            error = sqlite3.OperationalError("private SQL sentinel")
            error.sqlite_errorcode = getattr(sqlite3, "SQLITE_" + name)
            raise error
        with original() as conn:
            yield conn

    monkeypatch.setattr(ctx.store, "transaction", transaction)
    if path == "label_busy":
        original_record = ctx.store._muse_record_observation

        def record(*a, **kw):
            original_record(*a, **kw)
            failing["on"] = True

        monkeypatch.setattr(ctx.store, "_muse_record_observation", record)
    connector = (
        Connecting(transform=lambda key, nonce: rv(), before_reply=lambda: failing.update(on=True))
        if path == "rv"
        else Connecting()
    )
    transport, *_ = build(
        handler=ok_frames([]),
        store=ctx.store,
        connector=connector,
        http=FakeHttp({"/api/session": response(assignment(cvm_required=True))})
        if path == "hint"
        else None,
    )
    with pytest.raises(CoreError) as caught:
        if path in {"freeze", "label_busy"}:
            freeze(ctx.store, confidential=path == "label_busy")
        else:
            transport.sessions_list()
    detail = caught.value.detail
    assert detail.code == ErrorCode.STORAGE
    assert "sentinel" not in str(detail) and "SQL" not in str(detail)
    if name in RETRY:
        assert detail.retry_class == "safe_read"
        assert "Retry" in detail.next_action
    elif name in DAMAGE:
        assert "verified backup" in detail.next_action
    elif name in ENV + ["ENOSPC", "EROFS"]:
        assert detail.retry_class == "user_action"
        assert any(word in detail.next_action for word in ["permissions", "writable", "disk"])
        assert "restore" not in detail.next_action.lower()
    else:
        assert detail.next_action == "Report this internal Muse security-state failure"
        assert "restore" not in str(detail).lower() and "permissions" not in str(detail).lower()
    if path in {"hint", "rv"}:
        assert detail.message == "Muse confidentiality could not be recorded"
    elif name in INTERNAL:
        assert detail.message == "Muse security state internal failure"
    assert all(peer.msg3_payload is None for peer in connector.peers)


@pytest.mark.parametrize("version", [4, 5])
@pytest.mark.parametrize(
    "field,value",
    [
        ("generation", 1.5),
        ("generation", b"1"),
        ("generation", "x"),
        ("version", 1.5),
        ("confidential_required", b"1"),
        ("viewer_hash", b"a" * 64),
        ("session_hash", b"b" * 64),
        ("active_vm_id", b"vm1"),
        ("updated_at", ""),
        ("updated_at", b"t"),
    ],
)
def test_r5_7_types_checked_at_write(tmp_path, version, field, value):
    from metamuse.paths import WorkspacePaths
    from metamuse.store import Store, MIGRATIONS

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


def test_r5_7_amended_types_survive_backup_restore(ctx, tmp_path):
    from metamuse.paths import WorkspacePaths
    from metamuse.store import Store

    freeze(ctx.store, confidential=True)
    archive = tmp_path / "archive"
    ctx.store.backup(archive)
    restored = Store.restore(archive, WorkspacePaths.resolve(tmp_path / "restored")).store
    assert freeze(restored).confidential_required
    with pytest.raises(CoreError):
        with restored.transaction() as conn:
            conn.execute("UPDATE muse_trust_accounts SET generation=1.5")
