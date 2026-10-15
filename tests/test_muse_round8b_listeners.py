"""Listener failures finish invalidation and truthfully report the committed write."""

import hashlib
import sqlite3
from types import SimpleNamespace

import pytest
from test_muse_round8_notifications import freeze

from metamuse.models import CoreError, ErrorCode
from metamuse.muse import MuseTransport

INTERNAL = "Muse security state internal failure"
ACTION = "Report this internal Muse security-state failure"


class Listener:
    def __init__(self, calls, name, failure=None):
        self.calls, self.name, self.failure = calls, name, failure

    def close(self):
        self.calls.append(self.name)
        if self.failure is not None:
            raise self.failure


def assert_internal(error, cause):
    assert error.detail.code == ErrorCode.STORAGE
    assert error.detail.message == INTERNAL
    assert error.detail.next_action == ACTION
    assert error.detail.retry_class == "never"
    assert error.__cause__ is cause


def assert_committed(store, snapshot, observation_owner):
    with pytest.raises(CoreError) as caught:
        store.muse_assert_current(snapshot)
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    with store.connection() as conn:
        assert (
            conn.execute(
                "SELECT confidential_required FROM muse_trust_accounts "
                "WHERE provider=? AND account=?",
                (snapshot.provider, snapshot.account),
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT confidential_required FROM muse_trust_vms "
                "WHERE provider=? AND account=? AND vm_id=?",
                (snapshot.provider, snapshot.account, snapshot.vm_id),
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT confidential_required FROM muse_trust_observations "
                "WHERE viewer_hash=? AND vm_id=?",
                (observation_owner, snapshot.vm_id),
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize("path", ["direct", "inherited"])
@pytest.mark.parametrize("failure_kind", ["runtime", "sqlite_busy"])
def test_confidential_broadcast_finishes_after_listener_failure(ctx, path, failure_kind):
    affected = freeze(ctx.store, "b", "x")
    unrelated = freeze(ctx.store, "c", "z")
    if path == "inherited":
        freeze(ctx.store, confidential=True)
    failure = RuntimeError("synthetic listener failure")
    if failure_kind == "sqlite_busy":
        failure = sqlite3.OperationalError("synthetic callback busy")
        failure.sqlite_errorcode = sqlite3.SQLITE_BUSY
    calls = []
    first, second = Listener(calls, "first", failure), Listener(calls, "second")
    ctx.store.muse_watch(affected, first.close)
    ctx.store.muse_watch(unrelated, second.close)
    with pytest.raises(CoreError) as caught:
        freeze(ctx.store, vm_id="x", confidential=path == "direct")
    assert calls == ["first", "second"]
    assert_internal(caught.value, failure)
    # The newly authenticated edge belongs to A, but B's component became confidential.
    assert_committed(ctx.store, affected, hashlib.sha256(b"viewer-a").hexdigest())


def test_notify_raises_from_first_failure_after_all_callbacks(ctx):
    snapshots = [freeze(ctx.store, name, "vm-" + name) for name in ("a", "b", "c")]
    first_failure = ValueError("synthetic first callback")
    calls = []
    listeners = [
        Listener(calls, "first", first_failure),
        Listener(calls, "second", KeyboardInterrupt("synthetic second callback")),
        Listener(calls, "third"),
    ]
    for snapshot, listener in zip(snapshots, listeners, strict=True):
        ctx.store.muse_watch(snapshot, listener.close)
    with pytest.raises(CoreError) as caught:
        ctx.store._muse_notify("synthetic", "a", confidential=True)
    assert calls == ["first", "second", "third"]
    assert_internal(caught.value, first_failure)


class NoTransportLock:
    def acquire(self, *args, **kwargs):
        pytest.fail("trust invalidation must stay lock-free")

    def __enter__(self):
        pytest.fail("trust invalidation must stay lock-free")


class ClearingTransport(MuseTransport):
    """Model _close_locked clearing the field between the callback's two reads."""

    def __getattribute__(self, name):
        value = super().__getattribute__(name)
        if name == "_loop":
            assert self._trust_invalidated, "invalidation must precede the loop read"
            self._loop = None
        return value


def race_transport(calls):
    transport = ClearingTransport()
    transport._lock = NoTransportLock()
    link = Listener(calls, "transport")
    transport._loop = SimpleNamespace(link=link)
    return transport


def test_invalidate_trust_uses_one_loop_read_without_a_lock():
    calls = []
    transport = race_transport(calls)
    transport._invalidate_trust()
    assert transport._trust_invalidated
    assert transport._loop is None
    assert calls == ["transport"]


def test_invalidation_race_does_not_skip_another_workspace_listener(ctx):
    affected = freeze(ctx.store, "b", "x")
    unrelated = freeze(ctx.store, "c", "z")
    calls = []
    transport = race_transport(calls)
    other = Listener(calls, "other")
    ctx.store.muse_watch(affected, transport._invalidate_trust)
    ctx.store.muse_watch(unrelated, other.close)
    # A callback may execute in the writer/transport's own thread; no lock acquisition.
    result = freeze(ctx.store, vm_id="x", confidential=True)
    assert result.confidential_required
    assert transport._trust_invalidated and transport._loop is None
    assert calls == ["transport", "other"]
    assert_committed(ctx.store, affected, result.viewer_hash)


def test_record_confidential_listener_failure_keeps_committed_observation(ctx):
    affected = freeze(ctx.store, "b", "x")
    unrelated = freeze(ctx.store, "c", "z")
    failure = RuntimeError("synthetic callback failure after commit")
    calls = []
    first, second = Listener(calls, "first", failure), Listener(calls, "second")
    ctx.store.muse_watch(unrelated, first.close)
    ctx.store.muse_watch(affected, second.close)
    with pytest.raises(CoreError) as caught:
        ctx.store.muse_record_confidential(affected)
    assert_internal(caught.value, failure)
    assert calls == ["first", "second"]
    assert_committed(ctx.store, affected, affected.viewer_hash)
    with ctx.store.connection() as conn:
        row = conn.execute(
            "SELECT source FROM muse_trust_observations WHERE viewer_hash=? AND vm_id=?",
            (affected.viewer_hash, affected.vm_id),
        ).fetchone()
        assert row[0] == "noise_message_2"
