"""Remote trust invariants, independently reviewed synthetic regressions."""

import pytest
from test_muse_standard import Connecting
from test_muse_transport import build, ok_frames
from test_muse_trust import freeze

from metamuse._muse_trust import StoreMuseTrust
from metamuse.models import CoreError, ErrorCode


def refused(store, snapshot):
    sent = []
    with pytest.raises(CoreError) as caught:
        store.muse_standard_handoff(snapshot, lambda: sent.append("msg3"))
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert caught.value.detail.retry_class == "never"
    assert not sent


@pytest.mark.parametrize("provider", ["Synthetic", "synthetic ", "synthetic.alias"])
@pytest.mark.parametrize("identity", ["owner", "vm"])
@pytest.mark.parametrize("record", ["freeze", "rv"])
def test_r3_a_provider_labels_never_partition_remote_trust(ctx, provider, identity, record):
    first = freeze(ctx.store, confidential=record == "freeze")
    if record == "rv":
        ctx.store.muse_record_confidential(first)
    other = freeze(
        ctx.store,
        provider=provider,
        account="other-label",
        viewer_id="viewer" if identity == "owner" else "other-owner",
        vm_id="other-vm" if identity == "owner" else " VM1 ",
    )
    assert other.confidential_required
    refused(ctx.store, other)


@pytest.mark.parametrize("provider", ["Synthetic", "synthetic ", "synthetic.alias"])
@pytest.mark.parametrize("identity", ["owner", "vm"])
@pytest.mark.parametrize("record", ["freeze", "rv"])
def test_r3_a_transport_refuses_provider_alias_before_msg3(ctx, provider, identity, record):
    first = freeze(
        ctx.store,
        account="original",
        viewer_id="viewer-fixture",
        vm_id="other-vm" if identity == "owner" else "vm1",
        confidential=record == "freeze",
    )
    if record == "rv":
        ctx.store.muse_record_confidential(first)
    connector = Connecting()
    transport, _, _, _ = build(handler=ok_frames([]), connector=connector, store=ctx.store)
    transport._trust = StoreMuseTrust(ctx.store, provider, "other-label")
    if identity == "vm":
        from test_muse_standard import response

        transport._http.responses["/api/auth/check"] = response(
            {
                "ok": True,
                "access_token": "synthetic-token",
                "viewer_id": "other-owner",
                "session_binding_id": "binding-fixture",
            }
        )
    with pytest.raises(CoreError) as caught:
        with transport:
            transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert not connector.urls


@pytest.mark.parametrize("identity", ["owner", "vm"])
def test_r3_b_stale_rv_snapshot_records_before_refusal(ctx, identity):
    from metamuse.store import Store

    stale = freeze(ctx.store)
    freeze(Store(ctx.paths), session_binding_id="changed")
    ctx.store.muse_record_confidential(stale)
    later = freeze(
        ctx.store,
        account="later",
        viewer_id="viewer" if identity == "owner" else "other",
        vm_id="other" if identity == "owner" else "vm1",
    )
    assert later.confidential_required
    refused(ctx.store, later)


def test_r3_b_transport_records_rv_after_cross_process_staleness(ctx):
    from test_muse_standard import rv

    from metamuse.store import Store

    connector = Connecting(transform=lambda key, nonce: rv())
    transport, _, _, _ = build(handler=ok_frames([]), connector=connector, store=ctx.store)
    connector.kwargs["before_reply"] = lambda: freeze(
        Store(ctx.paths),
        account="synthetic",
        viewer_id="viewer-fixture",
        session_binding_id="concurrent",
    )
    with pytest.raises(CoreError) as caught:
        with transport:
            transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert connector.peers[0].msg3_payload is None
    later = freeze(
        ctx.store, provider="alias", account="later", viewer_id="viewer-fixture", vm_id="other"
    )
    assert later.confidential_required
    refused(ctx.store, later)


def test_r3_b_record_failure_stops_with_honest_error(ctx, monkeypatch):
    connector = Connecting()
    from test_muse_standard import rv

    connector.kwargs["transform"] = lambda key, nonce: rv()
    transport, _, _, _ = build(handler=ok_frames([]), connector=connector, store=ctx.store)

    original = ctx.store._muse_observe

    def cannot_record(*args, **kwargs):
        if kwargs["source"] == "noise_message_2":
            raise CoreError(ErrorCode.STORAGE, "synthetic busy")
        return original(*args, **kwargs)

    monkeypatch.setattr(ctx.store, "_muse_observe", cannot_record)
    with pytest.raises(CoreError) as caught:
        with transport:
            transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.STORAGE
    assert "confidentiality could not be recorded" in caught.value.detail.message
    assert connector.peers[0].msg3_payload is None


@pytest.mark.parametrize("identity", ["owner", "vm"])
def test_r3_c_hint_owner_mismatch_is_recorded_before_binding_refusal(ctx, identity):
    freeze(ctx.store, viewer_id="old-owner")
    with pytest.raises(CoreError) as caught:
        freeze(
            ctx.store,
            viewer_id="observed-owner",
            vm_id="cvm1",
            confidential=True,
            confidential_vm_ids=("cvm1", "cvm2", "cvm3"),
        )
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    later = freeze(
        ctx.store,
        provider="another",
        account="new-label",
        viewer_id="observed-owner" if identity == "owner" else "third-owner",
        vm_id="new-vm" if identity == "owner" else "cvm2",
    )
    assert later.confidential_required
    refused(ctx.store, later)


@pytest.mark.parametrize("identity", ["owner", "vm"])
def test_r3_c_transport_keeps_hint_under_mismatched_owner(ctx, identity):
    from test_muse_standard import assignment, response
    from test_muse_transport import FakeHttp

    freeze(ctx.store, account="synthetic", viewer_id="old-owner")
    http = FakeHttp(
        {
            "/api/session": response(
                assignment(
                    status="unassigned",
                    vms=[{"vm_id": " CVM2 ", "vm_type": "hatch_cvm"}, {"vm_id": "cvm3"}],
                )
            )
        }
    )
    connector = Connecting()
    transport, _, _, _ = build(
        handler=ok_frames([]), store=ctx.store, connector=connector, http=http
    )
    with pytest.raises(CoreError) as caught:
        with transport:
            transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert not connector.urls
    later = freeze(
        ctx.store,
        provider="another",
        account="later",
        viewer_id="viewer-fixture" if identity == "owner" else "third-owner",
        vm_id="vm-new" if identity == "owner" else "cvm2",
    )
    assert later.confidential_required
    refused(ctx.store, later)


@pytest.mark.parametrize("shape", ["owner", "vm"])
def test_r3_g_component_above_sqlite_recursion_limit_records_atomically(ctx, shape):
    from metamuse.store import MuseTrustSnapshot

    count = 1200
    with ctx.store.transaction() as conn:
        for i in range(count):
            viewer = "a" * 64 if shape == "owner" else f"{i:064x}"
            vm = f"vm{i}" if shape == "owner" else "vm1"
            conn.execute(
                "INSERT INTO muse_trust_accounts VALUES ('synthetic',?,1,?,? ,?,1,0,'t')",
                (str(i), viewer, "b" * 64, vm),
            )
            conn.execute(
                (
                    "INSERT INTO muse_trust_vms VALUES ('synthetic',?,?,1,0,'authentic"
                    "ated_assignment','t')"
                ),
                (str(i), vm),
            )
    snapshot = MuseTrustSnapshot(
        "synthetic",
        "0",
        "vm0" if shape == "owner" else "vm1",
        "a" * 64 if shape == "owner" else f"{0:064x}",
        "b" * 64,
        1,
        False,
    )
    ctx.store.muse_record_confidential(snapshot)
    with ctx.store.connection() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM muse_trust_accounts WHERE confidential_required=1"
            ).fetchone()[0]
            == count
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM muse_trust_vms WHERE confidential_required=1"
            ).fetchone()[0]
            == count
        )
        assert conn.execute("SELECT max(generation) FROM muse_trust_accounts").fetchone()[0] == 2
        assert not conn.execute("SELECT * FROM muse_trust_propagating").fetchall()
    refused(ctx.store, snapshot)


@pytest.mark.parametrize("shape", ["owner_then_vm", "vm_then_owner"])
@pytest.mark.parametrize("record", ["freeze", "rv"])
def test_r3_m_two_hop_components_raise_stored_rows_and_refuse(ctx, shape, record):
    if shape == "owner_then_vm":
        freeze(ctx.store, provider="Synthetic", account="B", viewer_id="V", vm_id="y")
        freeze(ctx.store, provider="synthetic ", account="C", viewer_id="W", vm_id="y")
        viewer, vm = "V", "z"
    else:
        freeze(ctx.store, provider="Synthetic", account="B", viewer_id="W", vm_id="x")
        freeze(ctx.store, provider="synthetic ", account="C", viewer_id="W", vm_id="y")
        viewer, vm = "V", "x"
    first = freeze(
        ctx.store,
        provider="synthetic.alias",
        account="A",
        viewer_id=viewer,
        vm_id=vm,
        confidential=record == "freeze",
    )
    if record == "rv":
        ctx.store.muse_record_confidential(first)
    with ctx.store.connection() as conn:
        assert (
            conn.execute(
                "SELECT confidential_required FROM muse_trust_accounts WHERE account='C'"
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT confidential_required FROM muse_trust_vms WHERE account='C'"
            ).fetchone()[0]
            == 1
        )
    later = freeze(ctx.store, provider="synthetic ", account="C", viewer_id="W", vm_id="y")
    refused(ctx.store, later)


def test_r3_m_hint_records_every_vm_for_another_owner(ctx):
    freeze(ctx.store, confidential=True, confidential_vm_ids=("vm1", " CVM2 ", "cvm3"))
    later = freeze(
        ctx.store, provider="another", account="other", viewer_id="other-owner", vm_id="cvm2"
    )
    assert later.confidential_required
    refused(ctx.store, later)


TRUST_TRIGGERS = {
    "muse_trust_account_no_replace",
    "muse_trust_account_guard",
    "muse_trust_account_retention",
    "muse_trust_vm_no_replace",
    "muse_trust_vm_guard",
    "muse_trust_vm_retention",
    "muse_trust_accounts_inherit_insert",
    "muse_trust_accounts_inherit_update",
    "muse_trust_accounts_propagate_insert",
    "muse_trust_accounts_propagate_update",
    "muse_trust_vms_inherit_insert",
    "muse_trust_vms_inherit_update",
    "muse_trust_vms_propagate_insert",
    "muse_trust_vms_propagate_update",
    "muse_trust_observation_no_replace",
    "muse_trust_observation_guard",
    "muse_trust_observation_retention",
    "muse_trust_observations_inherit_insert",
    "muse_trust_observations_inherit_update",
    "muse_trust_observations_propagate_insert",
    "muse_trust_observations_propagate_update",
}
TRUST_INDEXES = {
    "muse_trust_owner_expectation",
    "muse_trust_vm_expectation",
    "muse_trust_observation_vm",
}


def test_r3_m_every_005_trigger_and_index_is_pinned(ctx):
    with ctx.store.connection() as conn:
        assert {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_schema WHERE type='trigger' AND name LIKE 'muse_trust_%'"
            )
        } == TRUST_TRIGGERS
        assert {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_schema WHERE type='index' AND name LIKE 'muse_trust_%'"
            )
        } == TRUST_INDEXES


@pytest.mark.parametrize("guard", ["active", "normalized", "null_generation", "vm_update"])
def test_r3_m_raw_sql_defence_in_depth(ctx, guard):
    from metamuse import store as core

    freeze(ctx.store, account="A", viewer_id="V", vm_id="x", confidential=True)
    freeze(ctx.store, account="C", viewer_id="W", vm_id="y")
    freeze(ctx.store, account="O", viewer_id="Z", vm_id=None, confidential=True)
    statements = {
        "active": (
            "UPDATE muse_trust_accounts SET active_vm_id='x',generation=genera"
            "tion+1 WHERE account='C'"
        ),
        "normalized": (
            "INSERT INTO muse_trust_vms VALUES ('synthetic','A','VM9 ',1,1,'noise_message_2','t')"
        ),
        "null_generation": "UPDATE muse_trust_accounts SET active_vm_id='vm9' WHERE account='O'",
        "vm_update": "UPDATE muse_trust_vms SET source='noise_message_2' WHERE account='C'",
    }
    if guard == "vm_update":
        # Establish an inconsistent row without its propagation triggers, then restore
        # every trigger from the migration under test. The UPDATE guard is independent.
        definitions = [
            sql
            for sql in core._statements(core.MIGRATIONS[5].read_text())
            if sql.startswith("CREATE TRIGGER") and "_propagate_" in sql
        ]
        with ctx.store.transaction() as conn:
            for sql in definitions:
                conn.execute("DROP TRIGGER " + sql.split()[2])
            conn.execute(
                "UPDATE muse_trust_accounts SET confidential_required=1,generation"
                "=generation+1 WHERE account='C'"
            )
            for sql in definitions:
                conn.execute(sql)
    with pytest.raises(CoreError) as caught:
        with ctx.store.transaction() as conn:
            conn.execute(statements[guard])
    assert caught.value.detail.code == ErrorCode.REFERENCE_INVALID


@pytest.mark.parametrize("upgrade", [False, True])
def test_r3_m_amended_005_fresh_upgrade_and_restore_remote_observations(tmp_path, upgrade):
    from metamuse import store as core
    from metamuse.paths import WorkspacePaths
    from metamuse.store import Store

    paths = WorkspacePaths.resolve(tmp_path / "workspace")
    if upgrade:
        Store(paths, schema_version=4, migrations={n: core.MIGRATIONS[n] for n in range(1, 5)})
    store = Store(paths)
    freeze(store, viewer_id="original-owner")
    with pytest.raises(CoreError):
        freeze(
            store,
            viewer_id="observed-owner",
            vm_id="cvm1",
            confidential=True,
            confidential_vm_ids=("cvm1", "cvm2"),
        )
    backup = tmp_path / "backup"
    store.backup(backup)
    restored_paths = WorkspacePaths.resolve(tmp_path / "restored")
    Store.restore(backup, restored_paths)
    restored = Store(restored_paths)
    with restored.connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        assert (
            conn.execute(
                "SELECT min(confidential_required) FROM muse_trust_observations WH"
                "ERE vm_id IN ('cvm1','cvm2')"
            ).fetchone()[0]
            == 1
        )
    later = freeze(restored, provider="new", account="later", viewer_id="other", vm_id="cvm2")
    assert later.confidential_required
    refused(restored, later)


def test_r3_h_stale_context_advises_retry_instead_of_restore(ctx):
    from metamuse.store import Store

    snapshot = freeze(ctx.store)
    freeze(Store(ctx.paths), session_binding_id="concurrent")
    with pytest.raises(CoreError) as caught:
        ctx.store.muse_assert_current(snapshot)
    assert caught.value.detail.code == ErrorCode.STORAGE
    assert "Retry the connection" in caught.value.detail.next_action
    assert "restore" not in caught.value.detail.next_action.lower()


def test_r3_h_schema_restore_advice_warns_of_later_expectation_loss(ctx):
    snapshot = freeze(ctx.store)
    with ctx.store.connection() as conn:
        conn.execute("DROP TRIGGER muse_trust_vm_guard")
    with pytest.raises(CoreError) as caught:
        ctx.store.muse_assert_current(snapshot)
    assert caught.value.detail.code == ErrorCode.STORAGE
    assert "after that backup" in caught.value.detail.next_action


@pytest.mark.parametrize("table,shape", [("accounts", "owner_then_vm"), ("vms", "vm_then_owner")])
def test_r3_m_raw_sql_raises_both_sides_of_the_entire_component(ctx, table, shape):
    if shape == "owner_then_vm":
        freeze(ctx.store, account="B", viewer_id="V", vm_id="y")
        freeze(ctx.store, account="C", viewer_id="W", vm_id="y")
        freeze(ctx.store, account="A", viewer_id="V", vm_id="z")
    else:
        freeze(ctx.store, account="B", viewer_id="W", vm_id="x")
        freeze(ctx.store, account="C", viewer_id="W", vm_id="y")
        freeze(ctx.store, account="A", viewer_id="V", vm_id="x")
    with ctx.store.transaction() as conn:
        suffix = ",generation=generation+1" if table == "accounts" else ""
        conn.execute(
            f"UPDATE muse_trust_{table} SET confidential_required=1{suffix} WHERE account='A'"
        )
        assert (
            conn.execute(
                "SELECT confidential_required FROM muse_trust_accounts WHERE account='C'"
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT confidential_required FROM muse_trust_vms WHERE account='C'"
            ).fetchone()[0]
            == 1
        )


def test_r3_b_rv_record_survives_changed_label_owner(ctx):
    import hashlib

    snapshot = freeze(ctx.store)
    with ctx.store.transaction() as conn:
        guard = conn.execute(
            "SELECT sql FROM sqlite_schema WHERE name='muse_trust_account_guard'"
        ).fetchone()[0]
        conn.execute("DROP TRIGGER muse_trust_account_guard")
        conn.execute(
            "UPDATE muse_trust_accounts SET viewer_hash=?,generation=generation+1",
            (hashlib.sha256(b"different-owner").hexdigest(),),
        )
        conn.execute(guard)
    ctx.store.muse_record_confidential(snapshot)
    later = freeze(ctx.store, account="observed-owner-label", vm_id="new-vm")
    assert later.confidential_required
    refused(ctx.store, later)
