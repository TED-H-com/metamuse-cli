"""Immediate candidate closure and pre-frame recovery; local Stores and Noise peers."""

import threading
import time
from types import SimpleNamespace

import pytest
from test_muse_round8_notifications import freeze
from test_muse_round8c_registration import setup_connection
from test_muse_transport import Token, mutation

from metamuse import muse
from metamuse.models import CoreError, ErrorCode

ROUTES = ["read", "session.start", "chat.stream"]


def call(transport, route, token):
    if route == "read":
        return transport.sessions_list()
    prepared = mutation(
        route, {"topic": "synthetic"} if route == "session.start" else {"text": "synthetic"}
    )
    if route == "session.start":
        return transport.session_start(prepared, authorization=token)
    return transport.chat_stream(prepared, authorization=token)


def assert_recovered(transport, http, connector, token, route):
    assert token.calls == (route != "read")
    assert len(http.calls) == 8 and len(connector.urls) == 2
    assert connector.peers[0].closed and connector.peers[0].msg3_payload is None
    assert [len(peer.frames) for peer in connector.peers] == [0, 1]
    assert transport._pending_link is None
    assert transport._loop.link.raw is connector.peers[1]


def change_context(writer, kind):
    if kind == "confidential":
        freeze(writer, vm_id="vm1", confidential=True)
    else:
        writer.muse_freeze(
            provider="synthetic",
            account="synthetic",
            vm_id="vm1",
            viewer_id="viewer-fixture",
            session_binding_id="rotated-binding",
        )


def assert_refused(action, route, token, code, message=None):
    if route == "chat.stream":
        result = action()
        assert result.state == muse.FAILED_BEFORE_SEND
        assert not result.bytes_handed_off and result.error_code == code.value
    else:
        with pytest.raises(CoreError) as caught:
            action()
        assert caught.value.detail.code == code
        if message is not None:
            assert caught.value.detail.message == message
        if route != "read":
            assert isinstance(caught.value, muse.MuseNotSubmitted)
            assert not caught.value.bytes_handed_off
    assert token.calls == (route != "read")


def test_notification_closes_handshaking_candidate_when_it_returns(ctx, monkeypatch):
    transport, http, connector, writer = setup_connection(ctx)
    original = ctx.store.muse_standard_handoff
    observed = []

    def before_msg3(snapshot, handoff):
        if not observed:
            freeze(writer, confidential=True)
            peer = connector.peers[0]
            observed.append((transport._trust_invalidated, peer.closed, peer.msg3_payload))
        return original(snapshot, handoff)

    monkeypatch.setattr(ctx.store, "muse_standard_handoff", before_msg3)
    with transport:
        assert transport.sessions_list().status == 200
        assert observed == [(True, True, None)], observed
        assert len(connector.urls) == 2 and len(http.calls) == 8
        assert connector.peers[0].msg3_payload is None
        assert [len(peer.frames) for peer in connector.peers] == [0, 1]


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("phase", ["before_return", "registered", "before_msg3"])
def test_owned_candidate_boundary_recovers_before_single_action(ctx, monkeypatch, route, phase):
    transport, http, connector, writer = setup_connection(ctx)
    token, observed = Token(), []

    def notify(peer):
        freeze(writer, confidential=True)
        observed.append((peer.closed, peer.msg3_payload, len(peer.sent)))

    if phase == "before_return":
        original = connector.connect

        def before_return(*args, **kwargs):
            peer = original(*args, **kwargs)
            if len(connector.urls) == 1:
                notify(peer)  # Connector still owns it; no cancellation promise yet.
            return peer

        monkeypatch.setattr(connector, "connect", before_return)
    elif phase == "registered":
        original = type(transport).__setattr__

        def on_registration(self, name, value):
            original(self, name, value)
            if self is transport and name == "_pending_link" and value is not None and not observed:
                notify(connector.peers[0])

        monkeypatch.setattr(type(transport), "__setattr__", on_registration)
    else:
        original = ctx.store.muse_standard_handoff

        def before_msg3(snapshot, handoff):
            if not observed:
                notify(connector.peers[0])
            return original(snapshot, handoff)

        monkeypatch.setattr(ctx.store, "muse_standard_handoff", before_msg3)

    with transport:
        assert call(transport, route, token).bytes_handed_off
        assert observed == [(phase != "before_return", None, int(phase == "before_msg3"))]
        assert_recovered(transport, http, connector, token, route)
        if phase != "before_msg3":
            assert not connector.peers[0].sent  # No handshake use after late ownership.
    assert transport._pending_link is transport._loop is None


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("close_result", ["eof", "os_error", "network_error"])
def test_blocked_msg2_is_unblocked_by_notification_close(ctx, monkeypatch, route, close_result):
    transport, http, connector, writer = setup_connection(ctx)
    entered, closed = threading.Event(), threading.Event()
    deadline = time.monotonic() + 120
    original = connector.connect
    token, outcomes, errors = Token(), [], []

    def connect(*args, **kwargs):
        peer = original(*args, **kwargs)
        if len(connector.urls) == 1:
            close = peer.close

            def unblock():
                close()
                closed.set()

            def blocked_recv(timeout):
                entered.set()
                assert closed.wait(max(0, deadline - time.monotonic())), "msg2 close never arrived"
                if close_result == "os_error":
                    raise OSError("synthetic closed pipe")
                if close_result == "network_error":
                    raise CoreError(ErrorCode.NETWORK_TIMEOUT, "synthetic closed pipe")
                return None

            peer.close, peer.recv = unblock, blocked_recv
        return peer

    monkeypatch.setattr(connector, "connect", connect)

    def run():
        try:
            outcomes.append(call(transport, route, token))
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    with transport:
        worker.start()
        try:
            assert entered.wait(max(0, deadline - time.monotonic()))
            freeze(writer, confidential=True)
            assert closed.is_set() and connector.peers[0].closed  # On writer return.
        finally:
            # A defective callback must fail the test without leaving a blocked reader.
            closed.set()
            worker.join(max(0, deadline - time.monotonic()))
        assert not worker.is_alive(), "transport thread did not finish after close"
        assert not errors and len(outcomes) == 1
        assert outcomes[0].bytes_handed_off
        assert_recovered(transport, http, connector, token, route)


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("kind", ["binding", "confidential"])
@pytest.mark.parametrize("phase", ["before_return", "msg2", "before_msg3"])
def test_invalidated_old_snapshot_refuses_before_any_new_bootstrap(
    ctx, monkeypatch, route, kind, phase
):
    transport, http, connector, writer = setup_connection(ctx)
    original = connector.connect
    observed, token = [], Token()

    def notify(peer):
        change_context(writer, kind)
        observed.append(peer.closed)

    def connect(*args, **kwargs):
        peer = original(*args, **kwargs)
        assert len(connector.urls) == 1, "durable refusal was bypassed by a new bootstrap"
        if phase == "before_return":
            notify(peer)
        elif phase == "msg2":

            def recv(timeout):
                notify(peer)
                return None  # Exactly the EOF produced by immediate closure.

            peer.recv = recv
        return peer

    monkeypatch.setattr(connector, "connect", connect)
    if phase == "before_msg3":
        original_handoff = ctx.store.muse_standard_handoff

        def handoff(snapshot, send):
            notify(connector.peers[0])
            return original_handoff(snapshot, send)

        monkeypatch.setattr(ctx.store, "muse_standard_handoff", handoff)
    code = ErrorCode.STORAGE if kind == "binding" else ErrorCode.PROTOCOL_MISMATCH
    with transport:
        assert_refused(
            lambda: call(transport, route, token),
            route,
            token,
            code,
            "Muse connection context changed" if kind == "binding" else None,
        )
        assert observed == [phase != "before_return"]
        assert len(http.calls) == 4 and len(connector.urls) == 1
        assert connector.peers[0].closed and connector.peers[0].msg3_payload is None
        assert not connector.peers[0].frames
        assert transport._pending_link is transport._loop is None


@pytest.mark.parametrize("route", ROUTES)
def test_pending_invalidation_keeps_three_attempt_bound_and_never_sent(ctx, monkeypatch, route):
    transport, http, connector, writer = setup_connection(ctx)
    original = ctx.store.muse_standard_handoff
    closed_at_return, token = [], Token()

    def before_msg3(snapshot, handoff):
        assert len(connector.urls) <= 3, "invalidation connect bound was removed"
        freeze(writer, confidential=True)
        closed_at_return.append(connector.peers[-1].closed)
        return original(snapshot, handoff)

    monkeypatch.setattr(ctx.store, "muse_standard_handoff", before_msg3)
    with transport:
        assert_refused(
            lambda: call(transport, route, token),
            route,
            token,
            ErrorCode.NETWORK_TIMEOUT,
            "Muse connection failed",
        )
        assert closed_at_return == [True] * 3
        assert len(connector.urls) == 3 and len(http.calls) == 12
        assert all(
            peer.closed and peer.msg3_payload is None and not peer.frames
            for peer in connector.peers
        )
        assert transport._pending_link is transport._loop is None


@pytest.mark.parametrize(
    "failure", ["storage", "protocol", "protocol_exception", "scope", "bad_nonce", "bad_aead"]
)
def test_notification_does_not_mask_real_handshake_failure(ctx, monkeypatch, failure):
    transport, http, connector, writer = setup_connection(ctx)
    original = connector.connect

    def connect(*args, **kwargs):
        peer = original(*args, **kwargs)
        if failure in {"bad_nonce", "bad_aead"}:
            from test_muse_standard import standard

            if failure == "bad_nonce":
                peer.transform = lambda key, nonce: standard(key, b"w" * 32)
            else:
                peer.wire_transform = lambda data: data[:-1] + bytes([data[-1] ^ 1])
            peer.before_reply = lambda: freeze(writer, confidential=True)
        else:

            def recv(timeout):
                freeze(writer, confidential=True)
                if failure == "protocol_exception":
                    raise muse.ProtocolMismatch("synthetic protocol refusal")
                code = {
                    "storage": ErrorCode.STORAGE,
                    "protocol": ErrorCode.PROTOCOL_MISMATCH,
                    "scope": ErrorCode.SCOPE_REQUIRED,
                }[failure]
                raise CoreError(code, "synthetic fixed refusal")

            peer.recv = recv
        return peer

    monkeypatch.setattr(connector, "connect", connect)
    with transport:
        with pytest.raises(CoreError) as caught:
            transport.sessions_list()
        assert (
            caught.value.detail.code
            == {
                "storage": ErrorCode.STORAGE,
                "protocol": ErrorCode.PROTOCOL_MISMATCH,
                "protocol_exception": ErrorCode.PROTOCOL_MISMATCH,
                "scope": ErrorCode.SCOPE_REQUIRED,
                "bad_nonce": ErrorCode.PROTOCOL_MISMATCH,
                "bad_aead": ErrorCode.PROTOCOL_MISMATCH,
            }[failure]
        )
        assert len(http.calls) == 4 and len(connector.urls) == 1
        assert connector.peers[0].closed and connector.peers[0].msg3_payload is None
        assert not connector.peers[0].frames
        assert transport._pending_link is transport._loop is None


def test_publication_covers_pending_to_loop_ownership(ctx, monkeypatch):
    transport, http, connector, writer = setup_connection(ctx)
    original = type(transport).__setattr__
    observed = []

    def on_publish(self, name, value):
        original(self, name, value)
        if self is transport and name == "_loop" and value is not None and not observed:
            assert self._pending_link is value.link
            freeze(writer, confidential=True)
            observed.append(connector.peers[0].closed)

    monkeypatch.setattr(type(transport), "__setattr__", on_publish)
    with transport:
        assert transport.sessions_list().status == 200
        assert observed == [True]
        assert len(http.calls) == 8 and len(connector.urls) == 2
        assert [len(peer.frames) for peer in connector.peers] == [0, 1]
        assert transport._pending_link is None
    assert transport._loop is None


def test_callback_reads_pending_before_published_loop():
    closed = []
    promoted = SimpleNamespace(link=SimpleNamespace(close=lambda: closed.append("promoted")))

    class Promoting(muse.MuseTransport):
        def __getattribute__(self, name):
            if name == "_pending_link":
                # Promotion between the two callback reads: loop is published first.
                self._loop = promoted
                self._pending_link = None
                return None
            return super().__getattribute__(name)

    transport = Promoting()
    transport._invalidate_trust()
    assert transport._trust_invalidated and closed == ["promoted"]


def test_failed_old_attempt_cannot_drop_later_candidate_or_loop(ctx, monkeypatch):
    from test_muse_standard import Responder

    transport, _, connector, _ = setup_connection(ctx)
    later_raw = Responder()
    later = muse._ConnectCandidate(later_raw)
    later_loop = muse.ReceiveLoop(later)
    later_loop.__enter__()
    original = connector.connect

    def connect(*args, **kwargs):
        peer = original(*args, **kwargs)

        def recv(timeout):
            transport._pending_link = later
            transport._loop = later_loop
            raise OSError("synthetic old attempt abort")

        peer.recv = recv
        return peer

    monkeypatch.setattr(connector, "connect", connect)
    with transport:
        with pytest.raises(CoreError):
            transport.sessions_list()
        assert connector.peers[0].closed and not later_raw.closed
        assert transport._pending_link is later and transport._loop is later_loop
        assert later_loop._held
    assert later_raw.closed and not later_loop._held
    assert transport._pending_link is transport._loop is None


def test_normal_close_releases_unpublished_candidate():
    from test_muse_standard import Responder

    transport = muse.MuseTransport()
    peer = Responder()
    transport._pending_link = muse._ConnectCandidate(peer)
    transport.close()
    assert peer.closed and transport._pending_link is transport._loop is None


def test_failure_cleanup_retains_candidate_until_it_is_closed(ctx, monkeypatch):
    from test_muse_standard import standard

    transport, http, connector, writer = setup_connection(ctx)
    connector.kwargs["transform"] = lambda key, nonce: standard(key, b"w" * 32)
    original, observed = transport._drop, []

    def before_drop(loop, raw):
        assert transport._pending_link is loop.link
        freeze(writer, confidential=True)
        observed.append(raw.closed)
        original(loop, raw)

    monkeypatch.setattr(transport, "_drop", before_drop)
    with transport:
        with pytest.raises(CoreError) as caught:
            transport.sessions_list()
        assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
        assert observed == [True] and len(http.calls) == 4 and len(connector.urls) == 1
        assert transport._pending_link is transport._loop is None


@pytest.mark.parametrize("route", ROUTES)
def test_published_candidate_inflight_close_never_replays_action(ctx, monkeypatch, route):
    transport, http, connector, writer = setup_connection(ctx)
    original = ctx.store.muse_application_handoff
    observed, token = [], Token()

    def after_handoff(snapshot, handoff):
        original(snapshot, handoff)
        # Trust writes cannot recurse inside send's boundary. Notify immediately
        # after release, still before response collection, with one frame handed off.
        peer = connector.peers[0]
        assert transport._pending_link is None
        assert transport._loop.link.raw is peer and len(peer.frames) == 1
        freeze(writer, confidential=True)
        observed.append(peer.closed)
        peer.incoming.clear()  # EOF instead of an application response.

    monkeypatch.setattr(ctx.store, "muse_application_handoff", after_handoff)
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
        assert observed == [True]
        assert token.calls == (route != "read")
        assert len(http.calls) == 4 and len(connector.urls) == 1
        assert len(connector.peers[0].frames) == 1


@pytest.mark.parametrize(
    "code", [ErrorCode.STORAGE, ErrorCode.PROTOCOL_MISMATCH, ErrorCode.SCOPE_REQUIRED]
)
def test_early_notification_does_not_hide_returned_peer_refusal(ctx, monkeypatch, code):
    transport, http, connector, writer = setup_connection(ctx)
    original = connector.connect

    def before_return(*args, **kwargs):
        peer = original(*args, **kwargs)
        if len(connector.urls) == 1:
            freeze(writer, confidential=True)
        return peer

    verify = transport._verify_peer

    def refuse_peer(checked, peer):
        if checked.url.startswith("wss:"):
            raise CoreError(code, "synthetic fixed peer refusal")
        return verify(checked, peer)

    monkeypatch.setattr(connector, "connect", before_return)
    monkeypatch.setattr(transport, "_verify_peer", refuse_peer)
    with transport:
        with pytest.raises(CoreError) as caught:
            transport.sessions_list()
        assert caught.value.detail.code == code
        assert len(http.calls) == 4 and len(connector.urls) == 1
        assert connector.peers[0].closed and not connector.peers[0].sent
        assert transport._pending_link is transport._loop is None
