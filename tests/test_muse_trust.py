"""Disk-backed private trust, restart, migration and final-handoff serialization."""

import threading
import time
from contextlib import contextmanager
from dataclasses import replace

import pytest

from metamuse import store as core
from metamuse.models import CoreError, ErrorCode
from metamuse.paths import WorkspacePaths
from metamuse.store import Store


def freeze(store, **changes):
    values = dict(
        provider="synthetic",
        account="account",
        vm_id="vm1",
        viewer_id="viewer",
        session_binding_id="session",
    )
    values.update(changes)
    return store.muse_freeze(**values)


def test_standard_persistence_across_restart_and_auth_ref_rotation(ctx):
    snapshot = freeze(ctx.store)
    called = []
    ctx.store.muse_standard_handoff(snapshot, lambda called=called: called.append("msg3"))
    assert called == ["msg3"]
    restarted = Store(ctx.paths)
    again = freeze(restarted)
    assert again == snapshot  # auth_ref is intentionally absent from trust identity
    with restarted.connection() as conn:
        row = conn.execute("SELECT * FROM muse_trust_vms").fetchone()
        assert row["source"] == "noise_message_2"
        text = str(dict(conn.execute("SELECT * FROM muse_trust_accounts").fetchone()))
        assert "viewer_hash" in text and "session_hash" in text
        assert "'viewer'" not in text and "'session'" not in text
        assert "auth_ref" not in text


@pytest.mark.parametrize("evidence", ["assignment", "msg2"])
def test_sticky_confidential_does_not_clear_on_standard_restart_or_selection(ctx, evidence):
    snapshot = freeze(ctx.store, confidential=evidence == "assignment")
    if evidence == "msg2":
        ctx.store.muse_record_confidential(snapshot)
    restarted = Store(ctx.paths)
    for vm_id in ("vm1", "vm2"):
        standard = freeze(restarted, vm_id=vm_id, session_binding_id="rotated-auth-session")
        assert standard.confidential_required
        called = []
        with pytest.raises(CoreError):
            restarted.muse_standard_handoff(standard, lambda called=called: called.append("msg3"))
        assert not called
    with restarted.connection() as conn:
        assert conn.execute(
            "SELECT active_vm_id,confidential_required FROM muse_trust_accounts"
        ).fetchone()[:] == ("vm1", 1)
        assert (
            conn.execute(
                "SELECT confidential_required FROM muse_trust_vms WHERE vm_id='vm1'"
            ).fetchone()[0]
            == 1
        )


def test_owner_cannot_silently_rebind_configured_account(ctx):
    snapshot = freeze(ctx.store)
    with pytest.raises(CoreError) as caught:
        freeze(ctx.store, viewer_id="different-owner")
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert caught.value.detail.retry_class == "never"
    assert freeze(ctx.store) == snapshot
    assert not freeze(
        ctx.store, account="explicitly-other-account", viewer_id="different-owner"
    ).confidential_required


@pytest.mark.parametrize(
    "change",
    [{"generation": 99}, {"session_hash": "a" * 64}, {"vm_id": "other"}, {"viewer_hash": "a" * 64}],
)
def test_stale_cas_blocks_msg3(ctx, change):
    snapshot = freeze(ctx.store)
    called = []
    with pytest.raises(CoreError) as caught:
        ctx.store.muse_standard_handoff(
            replace(snapshot, **change), lambda called=called: called.append("msg3")
        )
    assert (
        caught.value.detail.code == ErrorCode.STORAGE and caught.value.detail.retry_class == "never"
    )
    assert not called


def test_selection_and_session_generation_invalidate_old_context(ctx):
    original = freeze(ctx.store)
    with pytest.raises(CoreError) as caught:
        freeze(ctx.store, vm_id="vm2")
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    new = freeze(ctx.store, session_binding_id="session-2")
    assert new.generation == original.generation + 1
    changed = freeze(ctx.store, session_binding_id="session-rotated")
    assert changed.generation == new.generation + 1
    for snapshot in (original, new):
        with pytest.raises(CoreError) as caught:
            ctx.store.muse_assert_current(snapshot)
        assert caught.value.detail.code == ErrorCode.STORAGE


def test_missing_database_is_not_recreated(ctx):
    snapshot = freeze(ctx.store)
    ctx.paths.database.unlink()
    with pytest.raises(CoreError) as caught:
        ctx.store.muse_assert_current(snapshot)
    assert caught.value.detail.code == ErrorCode.STORAGE
    assert not ctx.paths.database.exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "DROP TRIGGER muse_trust_vm_guard",
        "PRAGMA ignore_check_constraints=ON; UPDATE muse_trust_vms SET version=0",
        "UPDATE muse_trust_vms SET observed_at=''",
    ],
)
def test_corrupt_or_unsupported_trust_blocks(ctx, mutation):
    snapshot = freeze(ctx.store)
    with ctx.store.connection() as conn:
        original = None
        if "version=0" in mutation:
            original = conn.execute(
                "SELECT sql FROM sqlite_schema WHERE name='muse_trust_vm_guard'"
            ).fetchone()[0]
            conn.execute("DROP TRIGGER muse_trust_vm_guard")
        for statement in mutation.split(";"):
            conn.execute(statement)
        if original:
            conn.execute(original)
    with pytest.raises(CoreError) as caught:
        ctx.store.muse_standard_handoff(snapshot, lambda: pytest.fail("msg3 sent"))
    assert caught.value.detail.code == ErrorCode.STORAGE


def test_missing_active_vm_state_never_guesses_standard(ctx):
    freeze(ctx.store)
    with ctx.store.connection() as conn:
        trigger = conn.execute(
            "SELECT sql FROM sqlite_schema WHERE name='muse_trust_vm_retention'"
        ).fetchone()[0]
        conn.execute("DROP TRIGGER muse_trust_vm_retention")
        conn.execute("DELETE FROM muse_trust_vms")
        conn.execute(trigger)
    with pytest.raises(CoreError) as caught:
        freeze(ctx.store)
    assert caught.value.detail.code == ErrorCode.STORAGE


def test_unavailable_commit_never_hands_off(ctx, monkeypatch):
    snapshot = freeze(ctx.store)
    transaction = ctx.store.transaction

    @contextmanager
    def failing_commit():
        with transaction() as conn:
            yield conn
            raise OSError("synthetic storage failure")

    monkeypatch.setattr(ctx.store, "transaction", failing_commit)
    with pytest.raises(CoreError) as caught:
        ctx.store.muse_standard_handoff(snapshot, lambda: pytest.fail("msg3 sent"))
    assert caught.value.detail.code == ErrorCode.STORAGE
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    with ctx.store.connection() as conn:
        assert (
            conn.execute("SELECT source FROM muse_trust_vms").fetchone()[0]
            == "authenticated_assignment"
        )


def test_handoff_error_retains_transport_class(ctx):
    from metamuse._muse_link import IncompleteBody

    snapshot = freeze(ctx.store)

    def failed_handoff():
        raise IncompleteBody("deadline")

    with pytest.raises(IncompleteBody):
        ctx.store.muse_standard_handoff(snapshot, failed_handoff)


def test_generation_invalidates_existing_socket(ctx):
    class Socket:
        closed = False

        def close(self):
            self.closed = True

    socket = Socket()
    snapshot = freeze(ctx.store)
    ctx.store.muse_watch(snapshot, socket.close)
    another_store = Store(ctx.paths)
    freeze(another_store, session_binding_id="rotated")
    assert socket.closed


def test_final_handoff_serializes_selection_change_across_store_instances(ctx):
    deadline = time.monotonic() + 90

    def remaining():
        return max(0, deadline - time.monotonic())

    first = freeze(ctx.store)
    another_store = Store(ctx.paths)
    entered, release, changed, trying = (threading.Event() for _ in range(4))
    errors = []

    def handoff():
        entered.set()
        assert release.wait(remaining())

    def connect():
        try:
            ctx.store.muse_standard_handoff(first, handoff)
        except Exception as exc:
            errors.append(exc)

    def reselect():
        trying.set()
        freeze(another_store, session_binding_id="reselected-session")
        changed.set()

    one = threading.Thread(target=connect)
    two = threading.Thread(target=reselect)
    one.start()
    assert entered.wait(remaining())
    two.start()
    assert trying.wait(remaining())
    assert not changed.wait(0.05)
    release.set()
    one.join(remaining())
    two.join(remaining())
    assert not one.is_alive() and not two.is_alive() and changed.is_set() and not errors


def test_forward_005_upgrade_preserves_existing_core_and_all_guards(tmp_path):
    paths = WorkspacePaths.resolve(tmp_path / "upgrade")
    older = Store(paths, schema_version=4, migrations={n: core.MIGRATIONS[n] for n in range(1, 5)})
    with older.connection() as conn:
        guards = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_schema WHERE type='trigger'")
        }
    new = Store(paths)
    assert new.schema_version == 5
    with new.connection() as conn:
        now = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_schema WHERE type='trigger'")
        }
        assert guards <= now
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    assert not freeze(new).confidential_required


def test_sql_guards_reject_downgrade_delete_and_owner_rebinding(ctx):
    freeze(ctx.store, confidential=True)
    for sql in (
        "UPDATE muse_trust_accounts SET confidential_required=0",
        "UPDATE muse_trust_vms SET confidential_required=0",
        "UPDATE muse_trust_accounts SET viewer_hash='" + "a" * 64 + "'",
        "DELETE FROM muse_trust_accounts",
        "DELETE FROM muse_trust_vms",
    ):
        with pytest.raises(CoreError):
            with ctx.store.transaction() as conn:
                conn.execute(sql)


def test_final_handoff_allows_workspace_maintenance_after_commit(ctx):
    from metamuse.store import file_lock

    snapshot = freeze(ctx.store)
    entered, release, maintenance = (threading.Event() for _ in range(3))
    errors = []

    def handoff():
        entered.set()
        assert release.wait(3)

    def connect():
        try:
            ctx.store.muse_standard_handoff(snapshot, handoff)
        except Exception as exc:
            errors.append(exc)

    def maintain():
        with file_lock(ctx.paths.runtime / "maintenance.lock"):
            maintenance.set()

    one, two = threading.Thread(target=connect), threading.Thread(target=maintain)
    one.start()
    assert entered.wait(3)
    two.start()
    assert maintenance.wait(3)
    release.set()
    one.join(3)
    two.join(3)
    assert maintenance.is_set() and not errors and not one.is_alive() and not two.is_alive()


def test_vm_identity_is_normalized_across_restart(ctx):
    first = freeze(ctx.store, vm_id="VM1", confidential=True)
    assert first.vm_id == "vm1"
    restarted = Store(ctx.paths)
    assert freeze(restarted, vm_id="vm1").confidential_required
    with restarted.connection() as conn:
        assert conn.execute("SELECT count(*) FROM muse_trust_vms").fetchone()[0] == 1


@pytest.mark.parametrize("identity", ["owner", "vm"])
def test_r2_f1_cross_label_confidentiality_cannot_downgrade(ctx, identity):
    freeze(ctx.store, account="first", confidential=True)
    snap = freeze(
        ctx.store,
        account="alias",
        vm_id="other" if identity == "owner" else " VM1 ",
        viewer_id="viewer" if identity == "owner" else "another-owner",
    )
    assert snap.confidential_required
    for check in (
        ctx.store.muse_assert_current,
        lambda value: ctx.store.muse_standard_handoff(value, lambda: pytest.fail("msg3")),
    ):
        with pytest.raises(CoreError) as exc:
            check(snap)
        assert exc.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
        assert exc.value.detail.retry_class == "never"
    with ctx.store.connection() as conn:
        assert (
            conn.execute(
                "SELECT confidential_required FROM muse_trust_accounts WHERE account=?", ("alias",)
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT confidential_required FROM muse_trust_vms WHERE account=?", ("alias",)
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize("identity", ["owner", "vm"])
def test_r2_f1_existing_alias_inherits_confidential_transition(ctx, identity):
    old = freeze(
        ctx.store,
        account="alias",
        vm_id="other" if identity == "owner" else "vm1",
        viewer_id="viewer" if identity == "owner" else "another-owner",
    )
    freeze(ctx.store, account="first", confidential=True)
    with pytest.raises(CoreError) as exc:
        ctx.store.muse_assert_current(old)
    assert exc.value.detail.code == ErrorCode.PROTOCOL_MISMATCH


def test_r2_f7_maintenance_released_but_trust_lock_retained_at_msg3(ctx):
    from metamuse.models import payload_digest
    from metamuse.store import file_lock

    snap = freeze(ctx.store)

    def handoff():
        with file_lock(ctx.paths.runtime / "maintenance.lock", blocking=False):
            pass
        digest = payload_digest([snap.provider, snap.account])
        with pytest.raises(CoreError) as exc:
            with file_lock(ctx.paths.runtime / f"muse-trust-{digest}.lock", blocking=False):
                pass
        assert exc.value.detail.code == ErrorCode.JOB_BUSY

    ctx.store.muse_standard_handoff(snap, handoff)
    ctx.store.muse_record_confidential(snap)
    for check in (
        ctx.store.muse_assert_current,
        lambda value: ctx.store.muse_standard_handoff(value, lambda: pytest.fail("msg3")),
    ):
        with pytest.raises(CoreError) as exc:
            check(snap)
        assert exc.value.detail.code == ErrorCode.PROTOCOL_MISMATCH


def test_r2_f7_schema_verified_once_but_changed_schema_detected(ctx, monkeypatch):
    snap = freeze(ctx.store)
    calls = []
    original = core._verify_schema

    def verify(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(core, "_verify_schema", verify)
    for _ in range(4):
        ctx.store.muse_assert_current(snap)
    assert len(calls) <= 1
    with ctx.store.connection() as conn:
        conn.execute("DROP TRIGGER muse_trust_vm_retention")
    with pytest.raises(CoreError) as exc:
        ctx.store.muse_assert_current(snap)
    assert exc.value.detail.code == ErrorCode.STORAGE


def test_r2_f8_backup_restore_keeps_confidential_expectation(ctx, tmp_path):
    freeze(ctx.store, confidential=True)
    backup = tmp_path / "confidential-backup"
    ctx.store.backup(backup)
    paths = WorkspacePaths.resolve(tmp_path / "restored")
    Store.restore(backup, paths)
    restored = Store(paths)
    snap = freeze(restored, account="alias", vm_id="different-vm")
    assert snap.confidential_required
    with pytest.raises(CoreError) as exc:
        restored.muse_assert_current(snap)
    assert exc.value.detail.code == ErrorCode.PROTOCOL_MISMATCH


@pytest.mark.parametrize("identity", ["owner", "vm"])
def test_r2_f1_sql_refuses_zero_beside_matching_confidential_row(ctx, identity):
    import sqlite3

    freeze(ctx.store, account="first", confidential=True)
    if identity == "vm":
        freeze(ctx.store, account="other", viewer_id="other-owner", vm_id="vm2")
    table = "muse_trust_accounts" if identity == "owner" else "muse_trust_vms"
    with ctx.store.connection() as conn:
        row = dict(conn.execute(f"SELECT * FROM {table} WHERE account='first'").fetchone())
        row.update(
            account="other-alias" if identity == "owner" else "other", confidential_required=0
        )
        columns = list(row)
        with pytest.raises(sqlite3.IntegrityError, match="inherited confidentiality"):
            conn.execute(
                f"INSERT INTO {table} ({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)})",
                list(row.values()),
            )
