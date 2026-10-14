"""Isolate recursive-view edges and INSERT triggers without app-write masking."""

import hashlib

import pytest
from test_muse_trust import freeze

from metamuse.models import CoreError, ErrorCode


def h(value):
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.mark.parametrize("edge", ["observation", "vm_join"])
def test_r4_10_raw_edges_visible_only_to_recursive_view(ctx, edge):
    if edge == "observation":
        target = freeze(ctx.store, account="C", viewer_id="W", vm_id="y")
        with ctx.store.transaction() as conn:
            conn.execute(
                "INSERT INTO muse_trust_observations VALUES (?, 'y',1,0,'authenticated_assignment','t')",
                (h("V"),),
            )
            conn.execute("INSERT INTO muse_trust_propagating VALUES (1)")
            conn.execute(
                "INSERT INTO muse_trust_observations VALUES (?, '',1,1,'noise_message_2','t')",
                (h("V"),),
            )
            conn.execute("DELETE FROM muse_trust_propagating")
    else:
        freeze(ctx.store, account="A", viewer_id="V", vm_id="x")
        target = freeze(ctx.store, account="C", viewer_id="W", vm_id="y")
        with ctx.store.transaction() as conn:
            conn.execute(
                "INSERT INTO muse_trust_vms VALUES ('synthetic','A','y',1,0,'authenticated_assignment','t')"
            )
            conn.execute("INSERT INTO muse_trust_propagating VALUES (1)")
            conn.execute(
                "UPDATE muse_trust_accounts SET confidential_required=1,generation=generation+1 WHERE account='A'"
            )
            conn.execute("DELETE FROM muse_trust_propagating")
    called = []
    with pytest.raises(CoreError) as caught:
        ctx.store.muse_standard_handoff(target, lambda: called.append(True))
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert not called


def test_r4_10_qb_transaction_effective_before_physical_flags(ctx):
    freeze(ctx.store, account="A", viewer_id="V", vm_id="x")
    freeze(ctx.store, account="B", viewer_id="W", vm_id="x")
    freeze(ctx.store, account="C", viewer_id="W", vm_id="y")
    with ctx.store.transaction() as conn:
        conn.execute("INSERT INTO muse_trust_propagating VALUES (1)")
        conn.execute(
            "UPDATE muse_trust_accounts SET confidential_required=1,generation=generation+1 WHERE account='A'"
        )
        conn.execute("DELETE FROM muse_trust_propagating")
    snapshot = freeze(ctx.store, account="C", viewer_id="W", vm_id="y")
    assert snapshot.confidential_required
    called = []
    with pytest.raises(CoreError) as caught:
        ctx.store.muse_standard_handoff(snapshot, lambda: called.append(True))
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert not called


@pytest.mark.parametrize("trigger", ["account_insert", "vm_insert", "observation_insert"])
def test_r4_10_raw_insert_propagates_physical_flags(ctx, trigger):
    freeze(ctx.store, account="C", viewer_id="W", vm_id="y")
    with ctx.store.transaction() as conn:
        if trigger == "account_insert":
            conn.execute(
                "INSERT INTO muse_trust_accounts VALUES ('alias','A',1,?,?,'y',1,1,'t')",
                (h("V"), h("s")),
            )
        elif trigger == "vm_insert":
            # No app freeze: the raw VM INSERT must be the only raise.
            conn.execute(
                "INSERT INTO muse_trust_accounts VALUES ('alias','A',1,?,?,'x',1,0,'t')",
                (h("V"), h("s")),
            )
            conn.execute(
                "INSERT INTO muse_trust_vms VALUES ('alias','A','x',1,0,'authenticated_assignment','t')"
            )
            conn.execute(
                "INSERT INTO muse_trust_vms VALUES ('alias','A','y',1,1,'noise_message_2','t')"
            )
        else:
            conn.execute(
                "INSERT INTO muse_trust_observations VALUES (?, 'y',1,1,'noise_message_2','t')",
                (h("V"),),
            )
        flags = conn.execute(
            "SELECT confidential_required FROM muse_trust_accounts WHERE account='C'"
        ).fetchone()[0]
        assert flags == 1
        assert (
            conn.execute(
                "SELECT confidential_required FROM muse_trust_vms WHERE account='C'"
            ).fetchone()[0]
            == 1
        )
        assert not conn.execute("SELECT * FROM muse_trust_propagating").fetchall()


@pytest.mark.parametrize("timeout", [0, -1])
def test_r4_10_invalid_budget_does_not_initialize_msg1(monkeypatch, timeout):
    from metamuse._muse_link import IncompleteBody, ScriptedLink
    from metamuse._muse_noise import NoiseParty, handshake_standard

    party = NoiseParty("initiator")
    calls = []
    original = party.write

    def write(payload):
        calls.append(True)
        return original(payload)

    monkeypatch.setattr(party, "write", write)
    with pytest.raises(IncompleteBody):
        handshake_standard(
            party,
            ScriptedLink(),
            timeout=timeout,
            clock=lambda: 0,
            confidential=lambda: pytest.fail("confidential"),
            standard_handoff=lambda callback: pytest.fail("msg3"),
        )
    assert not calls
