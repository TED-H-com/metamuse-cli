"""Authenticated confidentiality discrimination precedes standard input caps."""

import pytest
from test_muse_standard import Connecting, Responder, assignment, response
from test_muse_transport import FakeHttp, build, ok_frames
from test_muse_trust import freeze

from metamuse._muse_codec import _bytes_field as bf
from metamuse._muse_codec import _iter_fields
from metamuse._muse_codec import _varint_field as vf
from metamuse.models import CoreError, ErrorCode


class BigRV(Responder):
    def send(self, data):
        if self.sent:
            return super().send(data)
        self.sent.append(data)
        self.nonce = list(_iter_fields(self.party.read(data)))[0][2]
        payload = bf(1, b"A" * 9000) + vf(2, 1) + bf(3, self.nonce)
        self.incoming.append(bytes(self.party.conn.write_message(payload)))


class BigConnecting(Connecting):
    def connect(self, url, *, checked, timeout):
        self.urls.append(url)
        peer = BigRV()
        self.peers.append(peer)
        return peer


def test_r3_d_oversized_rv_is_authenticated_recorded_and_refused(ctx):
    connector = BigConnecting()
    transport, _, _, _ = build(handler=ok_frames([]), connector=connector, store=ctx.store)
    with pytest.raises(CoreError) as caught:
        with transport:
            transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert connector.peers[0].msg3_payload is None
    later = freeze(ctx.store, provider="synthetic", account="other", viewer_id="other", vm_id="vm1")
    assert later.confidential_required
    with pytest.raises(CoreError):
        ctx.store.muse_standard_handoff(later, lambda: pytest.fail("msg3"))


def test_r3_d_session_above_64k_scans_all_hints(ctx):
    payload = assignment(vms=[{"vm_id": "cvm2", "vm_type": "hatch_cvm"}])
    payload["padding"] = "x" * 80000
    connector = Connecting()
    transport, _, _, _ = build(
        handler=ok_frames([]),
        store=ctx.store,
        connector=connector,
        http=FakeHttp({"/api/session": response(payload)}),
    )
    with pytest.raises(CoreError) as caught:
        with transport:
            transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert not connector.urls
    later = freeze(ctx.store, account="other", viewer_id="other", vm_id="cvm2")
    assert later.confidential_required


def test_r3_d_session_above_1m_names_bound_records_nothing_and_never_dials(ctx):
    connector = Connecting()
    payload = assignment(vm_type="hatch_cvm", padding="x" * (1024 * 1024))
    transport, _, _, _ = build(
        handler=ok_frames([]),
        store=ctx.store,
        connector=connector,
        http=FakeHttp({"/api/session": response(payload)}),
    )
    with pytest.raises(CoreError) as caught:
        with transport:
            transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.LIMIT_REACHED
    assert "session response" in caught.value.detail.message
    assert not connector.urls
    with ctx.store.connection() as conn:
        assert not conn.execute("SELECT * FROM muse_trust_observations").fetchall()
        assert not conn.execute("SELECT * FROM muse_trust_accounts").fetchall()


def test_r3_d_curl_session_body_uses_the_larger_bound(monkeypatch):
    from types import SimpleNamespace

    from curl_cffi.const import CurlOpt
    from test_muse_transport import PEER, checked_url

    from metamuse import _muse_http as http

    class Session:
        def __init__(self, **kwargs):
            pass

        def request(self, method, url, **kwargs):
            kwargs["content_callback"](b"x" * 80000)
            return SimpleNamespace(status_code=200, primary_ip=PEER, headers={})

        def close(self):
            pass

    monkeypatch.setattr(http, "load_curl", lambda: (Session, None, CurlOpt))
    url = "https://muse.ai/api/session?vm_state=live"
    result = http.CurlHttp().request(
        "GET", url, headers={}, body=None, checked=checked_url(url), timeout=1
    )
    assert len(result.body) == 80000
