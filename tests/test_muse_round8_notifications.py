"""Committed confidential edges close workspace sockets; unaffected calls reconnect."""

import time

import pytest
from test_muse_standard import Connecting
from test_muse_transport import Token, build, mutation, ok_frames

from metamuse.models import CoreError, ErrorCode


def freeze(store, account="a", vm_id="y", **kwargs):
    return store.muse_freeze(
        provider="synthetic",
        account=account,
        vm_id=vm_id,
        viewer_id="viewer-" + account,
        session_binding_id=kwargs.pop("session_binding_id", "session-" + account),
        **kwargs,
    )


class Watcher:
    def __init__(self, store, snapshot):
        self.calls = 0
        self.store = store
        self.snapshot = snapshot
        self.confidential = []
        store.muse_watch(snapshot, self.close)

    def close(self):
        self.calls += 1
        # Observe committed state, through a separate connection, inside the callback.
        with self.store.connection() as conn:
            self.confidential.append(
                conn.execute(
                    "SELECT confidential_required FROM muse_trust_accounts "
                    "WHERE provider=? AND account=?",
                    (self.snapshot.provider, self.snapshot.account),
                ).fetchone()[0]
            )


def test_inherited_confidential_edge_broadcasts_without_label_change(ctx):
    b = freeze(ctx.store, "b", "x")
    watcher = Watcher(ctx.store, b)
    a = freeze(ctx.store, confidential=True)
    assert watcher.calls == 1
    ctx.store.muse_assert_current(b)  # The first broadcast did not affect B's component.
    watcher.calls = 0
    inherited = freeze(ctx.store, vm_id="x")
    assert inherited.confidential_required
    assert inherited.generation == a.generation
    assert watcher.calls == 1
    assert watcher.confidential == [0, 1]
    with pytest.raises(CoreError) as caught:
        ctx.store.muse_assert_current(b)
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH


@pytest.mark.parametrize("confidential", [False, True])
def test_unchanged_refreeze_does_not_broadcast(ctx, confidential):
    unrelated = freeze(ctx.store, "b", "x")
    original = freeze(ctx.store, confidential=confidential)
    watcher = Watcher(ctx.store, unrelated)
    current = freeze(ctx.store)
    assert current == original
    assert watcher.calls == 0
    ctx.store.muse_assert_current(unrelated)


def test_changed_inherited_freeze_broadcasts_to_every_label(ctx):
    unrelated = freeze(ctx.store, "b", "x")
    original = freeze(ctx.store, confidential=True)
    own, other = Watcher(ctx.store, original), Watcher(ctx.store, unrelated)
    current = freeze(ctx.store, vm_id="z", session_binding_id="session-new")
    assert current.generation > original.generation
    assert own.calls == other.calls == 1
    assert other.confidential == [0]


def test_inherited_edge_closes_affected_transport_before_any_request(ctx):
    connector = Connecting()
    transport, http, *_ = build(handler=ok_frames([]), store=ctx.store, connector=connector)
    # Record direct evidence before opening B, so only the inherited edge closes B.
    freeze(ctx.store, confidential=True)
    with transport:
        transport.sessions_list()
        peer = connector.peers[0]
        calls = len(http.calls)
        freeze(ctx.store, vm_id="vm1")
        assert transport._trust_invalidated and peer.closed
        with pytest.raises(CoreError) as caught:
            transport.sessions_list()
        assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
        assert len(connector.urls) == 1 and len(http.calls) == calls
        assert len(peer.frames) == 1


@pytest.mark.parametrize("mutating", [False, True], ids=["read", "write"])
def test_unaffected_invalidated_transport_reconnects_once(ctx, mutating):
    frames = []
    transport, http, _, connector = build(handler=ok_frames(frames), store=ctx.store)
    with transport:
        assert transport.sessions_list().status == 200
        old_loop = transport._loop
        freeze(ctx.store, confidential=True)
        assert transport._trust_invalidated
        # The durable state is healthy; this global close is merely conservative.
        ctx.store.muse_assert_current(transport._snapshot)
        assert len(connector.urls) == 1 and len(http.calls) == 4
        if mutating:
            token = Token()
            result = transport.session_start(
                mutation("session.start", {"topic": "synthetic"}), authorization=token
            )
            assert token.calls == 1
            assert result.bytes_handed_off
            assert [frame.request.path for frame in frames].count("/session/start") == 1
        else:
            result = transport.sessions_list()
        assert result.status == 200
        assert "Store is not configured" not in repr(result)
        assert transport._loop is not old_loop and not old_loop._held
        assert not transport._trust_invalidated
        assert len(frames) == 2
        assert len(connector.urls) == 2 and len(http.calls) == 8
        assert [call["url"].split("/api/")[1].split("?")[0] for call in http.calls[4:]] == [
            "auth/check",
            "session",
            "hatch/token",
            "hatch/vm/wake",
        ]
    deadline = time.monotonic() + 30
    for thread in connector.threads:
        thread.join(timeout=max(0, deadline - time.monotonic()))
        assert not thread.is_alive()
    assert not connector.errors


def test_new_inherited_confidential_label_broadcasts(ctx):
    unrelated = freeze(ctx.store, "b", "x")
    freeze(ctx.store, confidential=True)
    watcher = Watcher(ctx.store, unrelated)
    inherited = ctx.store.muse_freeze(
        provider="alias",
        account="new",
        vm_id="z",
        viewer_id="viewer-a",
        session_binding_id="new-session",
    )
    assert inherited.confidential_required and watcher.calls == 1


def test_standard_binding_change_only_notifies_its_label(ctx):
    own_snapshot = freeze(ctx.store)
    other_snapshot = freeze(ctx.store, "b", "x")
    own, other = Watcher(ctx.store, own_snapshot), Watcher(ctx.store, other_snapshot)
    freeze(ctx.store, session_binding_id="new-session")
    assert own.calls == 1 and other.calls == 0


def test_broadcast_during_mutation_keeps_handoff_uncertain_without_replay(ctx):
    from metamuse.muse import MuseStateUnknown

    frames = []
    respond = ok_frames(frames)

    def handler(frame):
        if frame.request.path == "/session/start":
            frames.append(frame)
            freeze(ctx.store, confidential=True)
            return []  # The broadcast closed the socket before a response.
        return respond(frame)

    transport, http, _, connector = build(handler=handler, store=ctx.store)
    with transport:
        transport.sessions_list()
        with pytest.raises(MuseStateUnknown) as caught:
            transport.session_start(
                mutation("session.start", {"topic": "synthetic"}), authorization=Token()
            )
        assert caught.value.detail.code == ErrorCode.EXTERNAL_STATE_UNKNOWN
        assert caught.value.bytes_handed_off
        assert len(frames) == 2
        assert len(connector.urls) == 1 and len(http.calls) == 4
