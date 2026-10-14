"""Physical propagation and the post-commit label check have behavioural tests."""

import pytest
from test_muse_trust import freeze

from metamuse.models import CoreError, ErrorCode


def test_r5_10_close_updates_observations_physically(ctx):
    freeze(ctx.store, account="A", viewer_id="V", vm_id="x")
    freeze(ctx.store, account="B", viewer_id="W", vm_id="x")
    freeze(ctx.store, account="B2", viewer_id="W", vm_id="y")
    freeze(ctx.store, account="A", viewer_id="V", vm_id="x", confidential=True)
    with ctx.store.connection() as conn:
        assert all(
            r[0] == 1
            for r in conn.execute("SELECT confidential_required FROM muse_trust_observations")
        )


def test_r5_10_post_commit_session_change_is_context_storage(ctx, monkeypatch):
    from metamuse.store import Store

    freeze(ctx.store)
    other = Store(ctx.paths)
    original = ctx.store._muse_record_observation

    def record(*a, **kw):
        original(*a, **kw)
        freeze(other, session_binding_id="changed-session")

    monkeypatch.setattr(ctx.store, "_muse_record_observation", record)
    with pytest.raises(CoreError) as caught:
        freeze(ctx.store, confidential=True)
    assert caught.value.detail.code == ErrorCode.STORAGE
    assert caught.value.detail.message == "Muse connection context changed"


def test_r5_10_repair_example_preserves_an_extra_owner(ctx, monkeypatch):
    """Run the checked-in repair walkthrough with independent evidence present."""
    from test_muse_round4_barrier import test_r4_3_committed_barrier_has_safe_repair

    freeze(ctx.store, account="C", viewer_id="C", vm_id="z")
    close = ctx.store._muse_close_bind

    def closure_only(conn):
        assert (
            conn.execute(
                "SELECT confidential_required FROM muse_trust_accounts WHERE account='C'"
            ).fetchone()[0]
            == 0
        )
        close(conn)

    monkeypatch.setattr(ctx.store, "_muse_close_bind", closure_only)
    test_r4_3_committed_barrier_has_safe_repair(ctx)
