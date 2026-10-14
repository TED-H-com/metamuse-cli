"""A committed batching marker is refused and repaired without discarding trust."""

import pytest
from test_muse_trust import freeze

from metamuse.models import CoreError, ErrorCode


def test_r4_3_committed_barrier_has_safe_repair(ctx):
    snapshot = freeze(ctx.store)
    with ctx.store.transaction() as conn:
        conn.execute("INSERT INTO muse_trust_propagating VALUES (1)")
    for operation in [lambda: freeze(ctx.store), lambda: ctx.store.muse_assert_current(snapshot)]:
        with pytest.raises(CoreError) as caught:
            operation()
        assert caught.value.detail.code == ErrorCode.STORAGE
        assert (
            caught.value.detail.message == "Muse security state has a committed propagation barrier"
        )
        assert "whole workspace" in caught.value.detail.next_action
        assert "no restore is needed" in caught.value.detail.next_action
    with pytest.raises(CoreError) as caught:
        ctx.store.muse_record_confidential(snapshot)
    assert caught.value.detail.message == "Muse confidentiality could not be recorded"
    assert "whole workspace" in caught.value.detail.next_action
    assert "no restore is needed" in caught.value.detail.next_action
    # Human maintenance, with Muse connections stopped. No reset or trust deletion.
    with ctx.store.transaction() as conn:
        conn.execute(
            "UPDATE muse_trust_accounts SET confidential_required=1,generation=generation+1"
        )
        ctx.store._muse_close_bind(conn)
    with ctx.store.connection() as conn:
        assert not conn.execute("SELECT * FROM muse_trust_propagating").fetchall()
        assert conn.execute("SELECT confidential_required FROM muse_trust_vms").fetchone()[0] == 1
    assert freeze(ctx.store).confidential_required
