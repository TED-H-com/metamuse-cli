"""SQL boundaries: every component edge, inherit guards, types and lock errnos."""

import ast

import re

from pathlib import Path

import pytest

from test_muse_round5_storage import test_r5_7_fixed_storage_classes as storage_case

from test_muse_trust import freeze

from metamuse.models import CoreError, ErrorCode

@pytest.mark.parametrize("name", ["EACCES", "EPERM", "EIO", "ENOENT"])
@pytest.mark.parametrize("path", ["freeze", "transport", "hint", "rv", "label_busy"])
def test_r6_10_lock_file_access_errno(ctx, monkeypatch, name, path):
    storage_case(ctx, monkeypatch, name, path)

def h(value):
    import hashlib

    return hashlib.sha256(value.encode()).hexdigest()

def owner_of(conn, account):
    return tuple(
        conn.execute(
            "SELECT viewer_hash,session_hash FROM muse_trust_accounts WHERE account=?", (account,)
        ).fetchone()
    )

def owner_to_active_vm(conn):
    wh, sh = owner_of(conn, "C")
    conn.execute(
        "INSERT INTO muse_trust_accounts VALUES ('synthetic','D',1,?,?,'z',1,0,'t')", (wh, sh)
    )
    conn.execute("INSERT INTO muse_trust_propagating VALUES (1)")
    conn.execute(
        "INSERT INTO muse_trust_vms VALUES ('synthetic','E','z',1,1,'authenticated_assignment','t')"
    )
    conn.execute("DELETE FROM muse_trust_propagating")

def owner_to_label_vm(conn):
    wh, sh = owner_of(conn, "C")
    conn.execute(
        "INSERT INTO muse_trust_accounts VALUES ('synthetic','D',1,?,?,'d1',1,0,'t')", (wh, sh)
    )
    conn.execute(
        "INSERT INTO muse_trust_vms VALUES "
        "('synthetic','D','d1',1,0,'authenticated_assignment','t')"
    )
    conn.execute(
        "INSERT INTO muse_trust_vms VALUES ('synthetic','D','z',1,0,'authenticated_assignment','t')"
    )
    conn.execute("INSERT INTO muse_trust_propagating VALUES (1)")
    conn.execute(
        "INSERT INTO muse_trust_vms VALUES ('synthetic','E','z',1,1,'authenticated_assignment','t')"
    )
    conn.execute("DELETE FROM muse_trust_propagating")

def owner_to_observed_vm(conn):
    wh, _ = owner_of(conn, "C")
    conn.execute(
        "INSERT INTO muse_trust_observations VALUES (?,'z',1,0,'authenticated_assignment','t')",
        (wh,),
    )
    conn.execute("INSERT INTO muse_trust_propagating VALUES (1)")
    conn.execute(
        "INSERT INTO muse_trust_vms VALUES ('synthetic','E','z',1,1,'authenticated_assignment','t')"
    )
    conn.execute("DELETE FROM muse_trust_propagating")

def vm_to_active_owner(conn):
    conn.execute("INSERT INTO muse_trust_propagating VALUES (1)")
    conn.execute(
        "INSERT INTO muse_trust_accounts VALUES ('synthetic','F',1,?,?,'y',1,1,'t')",
        (h("U2"), h("s2")),
    )
    conn.execute("DELETE FROM muse_trust_propagating")

def vm_to_label_owner(conn):  # the existing vm_join variant
    conn.execute(
        "INSERT INTO muse_trust_vms VALUES ('synthetic','E','y',1,0,'authenticated_assignment','t')"
    )
    conn.execute("INSERT INTO muse_trust_propagating VALUES (1)")
    conn.execute(
        "UPDATE muse_trust_accounts SET confidential_required=1, generation=generation+1 "
        "WHERE account='E'"
    )
    conn.execute("DELETE FROM muse_trust_propagating")

def vm_to_observing_owner(conn):  # the existing observation variant
    conn.execute(
        "INSERT INTO muse_trust_observations VALUES (?,'y',1,0,'authenticated_assignment','t')",
        (h("V"),),
    )
    conn.execute("INSERT INTO muse_trust_propagating VALUES (1)")
    conn.execute(
        "INSERT INTO muse_trust_observations VALUES (?,'',1,1,'noise_message_2','t')", (h("V"),)
    )
    conn.execute("DELETE FROM muse_trust_propagating")

def control_unrelated(conn):
    conn.execute("INSERT INTO muse_trust_propagating VALUES (1)")
    conn.execute(
        "UPDATE muse_trust_accounts SET confidential_required=1, generation=generation+1 "
        "WHERE account='E'"
    )
    conn.execute("DELETE FROM muse_trust_propagating")

EDGES = [
    owner_to_active_vm,
    owner_to_label_vm,
    owner_to_observed_vm,
    vm_to_active_owner,
    vm_to_label_owner,
    vm_to_observing_owner,
]

@pytest.mark.parametrize("edge", EDGES)
def test_r6_11_each_seed_edge_is_effective(ctx, edge):
    target = freeze(ctx.store, account="C", viewer_id="W", vm_id="y")
    freeze(ctx.store, account="E", viewer_id="U", vm_id="u1")
    with ctx.store.transaction() as conn:
        edge(conn)
    # Freeze must report confidentiality, rather than an inherit-trigger E_STORAGE.
    assert freeze(ctx.store, account="C", viewer_id="W", vm_id="y").confidential_required
    calls = []
    with pytest.raises(CoreError) as caught:
        ctx.store.muse_standard_handoff(target, lambda: calls.append(1))
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH and not calls

@pytest.mark.parametrize("edge", EDGES)
@pytest.mark.parametrize("table", ["accounts", "vms", "observations"])
def test_r6_11_raw_inherit_edges(ctx, edge, table):
    freeze(ctx.store, account="C", viewer_id="W", vm_id="y")
    freeze(ctx.store, account="E", viewer_id="U", vm_id="u1")
    # Prepare the candidate's label before raising the remote component.
    with ctx.store.transaction() as conn:
        if table == "vms":
            conn.execute(
                "INSERT INTO muse_trust_accounts VALUES ('candidate','new',1,?,?,'y',1,0,'t')",
                (h("W"), h("s")),
            )
        edge(conn)
    with pytest.raises(CoreError):
        with ctx.store.transaction() as conn:
            if table == "accounts":
                conn.execute(
                    "INSERT INTO muse_trust_accounts VALUES ('candidate','new',1,?,?,'y',1,0,'t')",
                    (h("W"), h("s")),
                )
            elif table == "vms":
                conn.execute(
                    "INSERT INTO muse_trust_vms VALUES "
                    "('candidate','new','y',1,0,'authenticated_assignment','t')"
                )
            else:
                conn.execute(
                    "INSERT INTO muse_trust_observations VALUES "
                    "(?,'y',1,0,'authenticated_assignment','t')",
                    (h("W"),),
                )

@pytest.mark.parametrize(
    "table,field,value",
    [
        ("vms", "version", 1.5),
        ("vms", "version", b"1"),
        ("vms", "confidential_required", b"1"),
        ("vms", "vm_id", b"vm2"),
        ("vms", "observed_at", b"t"),
        ("vms", "source", b"authenticated_assignment"),
        ("observations", "viewer_hash", b"a" * 64),
        ("observations", "vm_id", b"vm2"),
        ("observations", "version", 1.5),
        ("observations", "confidential_required", b"1"),
        ("observations", "observed_at", b"t"),
        ("observations", "source", b"authenticated_assignment"),
    ],
)
def test_r6_11_all_trust_table_types(ctx, table, field, value):
    freeze(ctx.store)
    row = (
        dict(
            provider="synthetic",
            account="account",
            vm_id="vm2",
            version=1,
            confidential_required=0,
            source="authenticated_assignment",
            observed_at="t",
        )
        if table == "vms"
        else dict(
            viewer_hash="a" * 64,
            vm_id="vm2",
            version=1,
            confidential_required=0,
            source="authenticated_assignment",
            observed_at="t",
        )
    )
    row[field] = value
    with pytest.raises(CoreError):
        with ctx.store.transaction() as conn:
            conn.execute(
                "INSERT INTO muse_trust_" + table + " VALUES (" + ",".join("?" for _ in row) + ")",
                tuple(row.values()),
            )
