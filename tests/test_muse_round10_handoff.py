"""Application handoff shares the durable trust boundary; synthetic peers only."""

import sqlite3
import threading
import time

import pytest
from test_muse_round8_notifications import freeze
from test_muse_round8c_registration import setup_connection as _setup_connection
from test_muse_round9_pending import assert_refused, call, change_context
from test_muse_transport import Token

from metamuse import muse
from metamuse.models import CoreError, ErrorCode
from metamuse.store import SCHEMA_VERSION, file_lock

ROUTES = ["read", "session.start", "chat.stream"]


def setup_connection(ctx):
    result = _setup_connection(ctx)
    # These tests exercise ordering, not physical disk latency or time budgets.
    # Event waits below still have a real finite deadline to expose a deadlock.
    result[0]._clock = lambda: 0.0
    return result


@pytest.mark.parametrize("route", ROUTES)
def test_first_send_recovers_when_unrelated_notification_precedes_handoff(ctx, monkeypatch, route):
    transport, http, connector, writer = setup_connection(ctx)
    original = transport._unary
    observations, token = [], Token()

    def after_connect(*args, **kwargs):
        if not observations:
            freeze(writer, confidential=True)
            observations.append((transport._trust_invalidated, connector.peers[0].closed))
        return original(*args, **kwargs)

    monkeypatch.setattr(transport, "_unary", after_connect)
    with transport:
        assert call(transport, route, token).bytes_handed_off
        assert len(http.calls) == 8 and len(connector.urls) == 2
        assert observations == [(True, True)]
        assert [len(peer.frames) for peer in connector.peers] == [0, 1]
        assert token.calls == (route != "read")


@pytest.mark.parametrize("route", ROUTES)
def test_existing_loop_recovers_at_application_handoff(ctx, monkeypatch, route):
    transport, http, connector, writer = setup_connection(ctx)
    token = Token()
    with transport:
        transport.sessions_list()
        original = transport._unary

        def after_cached_connect(*args, **kwargs):
            freeze(writer, confidential=True)
            return original(*args, **kwargs)

        monkeypatch.setattr(transport, "_unary", after_cached_connect)
        assert call(transport, route, token).bytes_handed_off
        assert len(connector.urls) == 2 and len(http.calls) == 8
        assert connector.peers[0].closed
        assert [len(peer.frames) for peer in connector.peers] == [1, 1]
        assert token.calls == (route != "read")


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("kind,reuse", [("binding", False), ("confidential", True)])
def test_old_snapshot_refusal_at_handoff_never_bootstraps(ctx, monkeypatch, route, kind, reuse):
    transport, http, connector, writer = setup_connection(ctx)
    original = transport._unary
    token = Token()
    with transport:
        if reuse:
            transport.sessions_list()

        def after_connect(*args, **kwargs):
            change_context(writer, kind)
            return original(*args, **kwargs)

        monkeypatch.setattr(transport, "_unary", after_connect)
        code = ErrorCode.STORAGE if kind == "binding" else ErrorCode.PROTOCOL_MISMATCH
        assert_refused(
            lambda: call(transport, route, token),
            route,
            token,
            code,
            "Muse connection context changed" if kind == "binding" else None,
        )
        assert len(connector.urls) == 1 and len(http.calls) == 4
        assert connector.peers[0].closed
        assert len(connector.peers[0].frames) == int(reuse)


@pytest.mark.parametrize(
    "route,mixed", [(route, False) for route in ROUTES] + [("chat.stream", True)]
)
def test_application_and_handshake_invalidation_share_three_attempts(
    ctx, monkeypatch, route, mixed
):
    transport, http, connector, writer = setup_connection(ctx)
    token = Token()
    original_connect = connector.connect

    def bounded_connect(*args, **kwargs):
        assert len(connector.urls) < 4, "more than four dials: connect budget was reset"
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(connector, "connect", bounded_connect)
    if mixed:
        original_handoff = ctx.store.muse_standard_handoff

        def invalidate_first_handshake(snapshot, handoff):
            if len(connector.urls) == 1:
                freeze(writer, confidential=True)
            return original_handoff(snapshot, handoff)

        monkeypatch.setattr(ctx.store, "muse_standard_handoff", invalidate_first_handshake)
    original_application = ctx.store.muse_application_handoff

    def before_boundary(*args, **kwargs):
        freeze(writer, confidential=True)
        return original_application(*args, **kwargs)

    monkeypatch.setattr(ctx.store, "muse_application_handoff", before_boundary)
    with transport:
        assert_refused(
            lambda: call(transport, route, token),
            route,
            token,
            ErrorCode.NETWORK_TIMEOUT,
            "Muse connection failed",
        )
        assert len(connector.urls) == 3 and len(http.calls) == 12
        assert all(peer.closed and not peer.frames for peer in connector.peers)


@pytest.mark.parametrize("route", ROUTES)
def test_application_wins_boundary_then_write_is_inflight_never_replayed(ctx, monkeypatch, route):
    transport, http, connector, writer = setup_connection(ctx)
    original = transport._unary
    attempted, committed = threading.Event(), threading.Event()
    blocked, errors, ordering = [], [], []
    deadline = time.monotonic() + 180
    worker = None
    token = Token()

    def remaining():
        return max(0, deadline - time.monotonic())

    def write():
        try:
            try:
                with file_lock(writer.paths.runtime / "muse-trust-domain.lock", blocking=False):
                    blocked.append(False)
            except CoreError as exc:
                assert exc.detail.code == ErrorCode.JOB_BUSY
                blocked.append(True)
            finally:
                attempted.set()
            freeze(writer, confidential=True)
            ordering.append("write")
        except BaseException as exc:
            errors.append(exc)
        finally:
            committed.set()

    def after_connect(*args, **kwargs):
        loop = transport._loop
        original_open = loop.open_request

        def before_first_open(**request):
            nonlocal worker
            worker = threading.Thread(target=write, daemon=True)
            worker.start()
            assert attempted.wait(remaining()), "writer did not attempt the trust boundary"
            assert blocked == [True], "application send escaped the trust boundary"
            assert not committed.is_set()
            stream_id = original_open(**request)
            ordering.append("send")
            return stream_id

        def after_handoff(*args, **kwargs):
            # The trust locks have been released. The later notification is in-flight.
            assert committed.wait(remaining()), "writer stayed blocked after handoff"
            assert connector.peers[0].closed
            connector.peers[0].incoming.clear()
            return original_collect(*args, **kwargs)

        original_collect = loop.collect
        monkeypatch.setattr(loop, "open_request", before_first_open)
        monkeypatch.setattr(loop, "collect", after_handoff)
        return original(*args, **kwargs)

    monkeypatch.setattr(transport, "_unary", after_connect)
    try:
        with transport:
            if route == "chat.stream":
                result = call(transport, route, token)
                assert result.state == muse.SEND_UNCERTAIN and result.bytes_handed_off
            else:
                with pytest.raises(CoreError) as caught:
                    call(transport, route, token)
                if route == "session.start":
                    assert isinstance(caught.value, muse.MuseStateUnknown)
                    assert caught.value.bytes_handed_off
                else:
                    assert caught.value.detail.code == ErrorCode.NETWORK_TIMEOUT
            assert blocked == [True] and not errors
            assert ordering == ["send", "write"]
            assert len(connector.urls) == 1 and len(http.calls) == 4
            assert len(connector.peers[0].frames) == 1
            assert token.calls == (route != "read")
    finally:
        if worker is not None:
            worker.join(remaining())
            assert not worker.is_alive(), "writer did not exit"


@pytest.mark.parametrize(
    "failure",
    [
        muse.SendUncertain("synthetic send uncertainty"),
        muse.ProtocolMismatch("synthetic protocol refusal"),
        muse.IncompleteBody("synthetic incomplete read"),
        CoreError(ErrorCode.STORAGE, "synthetic callback storage failure"),
    ],
)
def test_application_callback_error_is_not_reclassified_by_store(ctx, failure):
    snapshot = freeze(ctx.store)

    def handoff():
        raise failure

    with pytest.raises(type(failure)) as caught:
        ctx.store.muse_application_handoff(snapshot, handoff)
    assert caught.value is failure


def test_application_boundary_releases_sqlite_and_maintenance_without_msg3_bookkeeping(ctx):
    snapshot = freeze(ctx.store)
    with ctx.store.connection() as conn:
        before = tuple(conn.execute("SELECT source,observed_at FROM muse_trust_vms").fetchone())
    calls = []

    def handoff():
        with file_lock(ctx.paths.runtime / "maintenance.lock", blocking=False):
            pass
        with sqlite3.connect(ctx.paths.database, timeout=0) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
            conn.execute("BEGIN IMMEDIATE")
            conn.rollback()
        calls.append(1)

    ctx.store.muse_application_handoff(snapshot, handoff)
    with ctx.store.connection() as conn:
        after = tuple(conn.execute("SELECT source,observed_at FROM muse_trust_vms").fetchone())
    assert calls == [1] and after == before


@pytest.mark.parametrize("route", ROUTES)
def test_genuine_uncertain_send_is_not_replayed_or_reclassified(ctx, monkeypatch, route):
    transport, http, connector, _ = setup_connection(ctx)
    original = transport._unary
    token = Token()

    def after_connect(*args, **kwargs):
        peer = connector.peers[0]
        send = peer.send

        def uncertain(data):
            send(data)  # The pipe accepted the action before reporting uncertainty.
            raise muse.SendUncertain("synthetic partial write")

        monkeypatch.setattr(peer, "send", uncertain)
        return original(*args, **kwargs)

    monkeypatch.setattr(transport, "_unary", after_connect)
    with transport:
        if route == "chat.stream":
            result = call(transport, route, token)
            assert result.state == muse.SEND_UNCERTAIN and result.bytes_handed_off
        else:
            with pytest.raises(muse.MuseStateUnknown) as caught:
                call(transport, route, token)
            assert caught.value.detail.code == ErrorCode.EXTERNAL_STATE_UNKNOWN
            assert caught.value.bytes_handed_off
        assert len(connector.urls) == 1 and len(http.calls) == 4
        assert len(connector.peers[0].frames) == 1
        assert token.calls == (route != "read")
