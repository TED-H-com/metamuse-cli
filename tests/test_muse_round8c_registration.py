"""Deterministic pre-frame notification barriers; all peers and writes are local."""

import pytest
from test_muse_round8_notifications import freeze
from test_muse_standard import Connecting
from test_muse_transport import Token, build, mutation, ok_frames

from metamuse import store as store_module
from metamuse._muse_trust import SelectedVM
from metamuse.models import CoreError, ErrorCode
from metamuse.muse import FAILED_BEFORE_SEND, MuseNotSubmitted
from metamuse.store import Store


def setup_connection(ctx):
    connector = Connecting()
    transport, http, _, _ = build(handler=ok_frames([]), store=ctx.store, connector=connector)
    writer = Store(ctx.store.paths)
    return transport, http, connector, writer


def once(callback):
    fired = False

    def fire():
        nonlocal fired
        if not fired:
            fired = True
            callback()

    return fire


def assert_fresh_only(connector):
    assert len(connector.urls) == 2
    assert connector.peers[0].closed
    assert [len(peer.frames) for peer in connector.peers] == [0, 1]
    assert connector.peers[1].frames[0].request.path == "/api/session/list"


def test_after_publish_before_final_check_reconnects(ctx, monkeypatch):
    transport, http, connector, writer = setup_connection(ctx)
    fire = once(lambda: freeze(writer, confidential=True))
    original = type(transport).__setattr__

    def on_publish(self, name, value):
        original(self, name, value)
        if self is transport and name == "_snapshot" and self._loop is not None:
            fire()

    monkeypatch.setattr(type(transport), "__setattr__", on_publish)
    with transport:
        assert transport.sessions_list().status == 200
        assert_fresh_only(connector)
        assert len(http.calls) == 8  # Both connects do the entire four-request bootstrap.


def test_inside_final_currentness_read_reconnects(ctx, monkeypatch):
    transport, _, connector, writer = setup_connection(ctx)
    fire = once(lambda: freeze(writer, confidential=True))
    original = ctx.store.muse_assert_current

    def before_read(snapshot):
        if transport._loop is not None:
            fire()
        return original(snapshot)

    monkeypatch.setattr(ctx.store, "muse_assert_current", before_read)
    with transport:
        assert transport.sessions_list().status == 200
        assert_fresh_only(connector)


def test_handshake_notification_reconnects(ctx, monkeypatch):
    transport, _, connector, writer = setup_connection(ctx)
    fire = once(lambda: freeze(writer, confidential=True))
    original = ctx.store.muse_standard_handoff

    def before_handoff(snapshot, handoff):
        fire()  # Before taking the trust lock, while the Noise handshake is active.
        return original(snapshot, handoff)

    monkeypatch.setattr(ctx.store, "muse_standard_handoff", before_handoff)
    with transport:
        assert transport.sessions_list().status == 200
        assert_fresh_only(connector)


def test_after_registration_before_mint_reconnects(ctx, monkeypatch):
    transport, _, connector, writer = setup_connection(ctx)
    fire = once(lambda: freeze(writer, confidential=True))
    original = transport._mint

    def before_mint(*args):
        fire()
        return original(*args)

    monkeypatch.setattr(transport, "_mint", before_mint)
    with transport:
        assert transport.sessions_list().status == 200
        assert_fresh_only(connector)


def test_label_scoped_change_during_connect_refuses_before_frame(ctx, monkeypatch):
    transport, _, connector, writer = setup_connection(ctx)
    other, _, _, _ = build(handler=ok_frames([]), store=writer, connector=Connecting())
    original = ctx.store.muse_standard_handoff

    def change_label(snapshot, handoff):
        other._trust.freeze(
            {"viewer_id": "viewer-fixture", "session_binding_id": "changed-binding"},
            SelectedVM("vm1", "vm1", "wss://vm1.metaaivm.com/", "hatch_vm", "RUNNING", False),
        )
        assert transport._trust_invalidated
        return original(snapshot, handoff)

    monkeypatch.setattr(ctx.store, "muse_standard_handoff", change_label)
    with transport, other:
        with pytest.raises(CoreError) as caught:
            transport.sessions_list()
        assert caught.value.detail.code == ErrorCode.STORAGE
        assert caught.value.detail.message == "Muse connection context changed"
        assert len(connector.urls) == 1 and connector.peers[0].closed
        assert not connector.peers[0].frames


def test_new_label_own_freeze_does_not_invalidate_first_connect(ctx, monkeypatch):
    transport, _, connector, _ = setup_connection(ctx)
    original = ctx.store.muse_freeze

    def freeze_with_own_notification(**kwargs):
        snapshot = original(**kwargs)
        # Model the label-scoped notification during freeze, before it returns.
        # A new standard bind currently need not emit one, so inject it explicitly.
        ctx.store._muse_notify(snapshot.provider, snapshot.account)
        return snapshot

    monkeypatch.setattr(ctx.store, "muse_freeze", freeze_with_own_notification)
    with transport:
        assert transport.sessions_list().status == 200
        assert len(connector.urls) == 1
        assert len(connector.peers[0].frames) == 1
        assert not transport._trust_invalidated


@pytest.mark.parametrize("route", ["read", "session.start", "chat.stream"])
def test_persistent_invalidation_stops_at_three_connects_never_sent(ctx, monkeypatch, route):
    transport, _, connector, writer = setup_connection(ctx)
    original_connect = connector.connect
    original_handoff = ctx.store.muse_standard_handoff

    def bounded_connector(*args, **kwargs):
        if len(connector.urls) >= 10:
            pytest.fail("reconnect bound missing: more than 10 WSS dials")
        return original_connect(*args, **kwargs)

    def invalidate_every_connect(snapshot, handoff):
        freeze(writer, confidential=True)
        return original_handoff(snapshot, handoff)

    monkeypatch.setattr(connector, "connect", bounded_connector)
    monkeypatch.setattr(ctx.store, "muse_standard_handoff", invalidate_every_connect)
    token = Token()
    with transport:
        if route == "chat.stream":
            result = transport.chat_stream(
                mutation(route, {"text": "synthetic"}), authorization=token
            )
            assert result.state == FAILED_BEFORE_SEND
            assert not result.bytes_handed_off
            assert result.error_code == ErrorCode.NETWORK_TIMEOUT.value
        else:
            with pytest.raises(CoreError) as caught:
                if route == "read":
                    transport.sessions_list()
                else:
                    transport.session_start(
                        mutation(route, {"topic": "synthetic"}), authorization=token
                    )
            assert caught.value.detail.code == ErrorCode.NETWORK_TIMEOUT
            assert caught.value.detail.message == "Muse connection failed"
            assert caught.value.detail.retry_class == "safe_read"
            assert caught.value.detail.next_action == (
                "Retry the connection. No gateway request frame was sent."
            )
            if route != "read":
                assert isinstance(caught.value, MuseNotSubmitted)
                assert not caught.value.bytes_handed_off
        assert token.calls == (route != "read")
        assert len(connector.urls) == 3
        assert all(peer.closed and not peer.frames for peer in connector.peers)
        assert transport._loop is None


def test_hundred_reconnects_keep_one_live_registration(ctx):
    transport, _, connector, writer = setup_connection(ctx)
    with transport:
        transport.sessions_list()
        for _ in range(100):
            freeze(writer, confidential=True)
            transport.sessions_list()
        key = (str(ctx.store.paths.root), "synthetic", "synthetic")
        with store_module._MUSE_LISTENERS_LOCK:
            refs = store_module._MUSE_LISTENERS[key]
            assert sum(ref() == transport._invalidate_trust for ref in refs) == 1
        assert len(connector.urls) == 101
        assert all(peer.closed and len(peer.frames) == 1 for peer in connector.peers[:-1])
        assert len(connector.peers[-1].frames) == 1
