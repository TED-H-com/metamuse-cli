"""Synthetic mature Noise responder and connect/trust/privacy parity. No network."""

import base64
import json
from urllib.parse import parse_qs, quote, urlsplit
from uuid import UUID

import pytest
from test_muse_transport import PEER, FakeHttp, Token, build, checked_url, mutation, ok_frames

from metamuse._muse_adapters import MuseURLGuard
from metamuse._muse_codec import (
    ApplicationResponse,
    Header,
    ProtocolMismatch,
    ServiceFrame,
    _iter_fields,
    decode_client_frame,
    encode_server_frame,
)
from metamuse._muse_codec import (
    _bytes_field as bf,
)
from metamuse._muse_codec import (
    _varint_field as vf,
)
from metamuse._muse_handshake_payloads import (
    ConfidentialChallenge,
    StandardBundle,
    classify_payload,
)
from metamuse._muse_http import CurlConnector, UpgradeFailure, _options, resolve_entries
from metamuse._muse_noise import NoiseParty, finished_pair
from metamuse._muse_redact import sanitize_document, sanitize_text
from metamuse.models import CoreError, ErrorCode, canonical_json


def standard(key=b"k" * 32, nonce=b"n" * 32):
    return bf(2, bf(1, key) + bf(2, nonce))


def rv(state=1, binding=b""):
    payload = bf(1, b"synthetic-attestation") + vf(2, state) + bf(3, b"n" * 32)
    if state == 2:
        payload += bf(4, b"p" * 32)
    if binding:
        payload += bf(5, binding)
    return payload


@pytest.mark.parametrize("payload", [standard(), vf(1, 0) + standard()])
def test_strict_standard_classifier(payload):
    assert classify_payload(payload) == StandardBundle(b"k" * 32, b"n" * 32)


@pytest.mark.parametrize(
    "payload",
    [rv(), rv(2), rv() + bf(9, b"extension"), rv(2, bf(1, b"chip") + bf(2, b"certificate"))],
)
def test_valid_rv_classifier(payload):
    assert isinstance(classify_payload(payload), ConfidentialChallenge)


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"arbitrary",
        b"\x12\x80",
        b"\x00",
        standard() + b"\x80",
        standard() + bf(2, b"duplicate"),
        vf(1, 1) + standard(),
        vf(1, 9) + standard(),
        vf(1, 0) + vf(1, 0) + standard(),
        bf(1, b"wrong-wire") + standard(),
        standard() + bf(10, b"SNP"),
        standard() + bf(20, b"manifest"),
        standard() + bf(21, b"proof"),
        standard() + bf(22, b"revocation"),
        bf(2, bf(1, b"k" * 32)),
        bf(2, bf(1, b"k" * 31) + bf(2, b"n" * 32)),
        bf(2, bf(1, b"k" * 32) + vf(2, 1)),
        bf(2, bf(1, b"k" * 32) * 2 + bf(2, b"n" * 32)),
        bf(2, bf(1, b"k" * 32) + bf(2, b"n" * 32) + bf(3, b"unknown")),
        rv(0),
        rv(3),
        rv() + bf(4, b"p" * 32),
        bf(1, b"attest") + vf(2, 2) + bf(3, b"n" * 32),
        bf(1, b"attest") + vf(2, 2) + bf(3, b"n" * 32) + bf(4, b"p"),
        rv() + vf(2, 1),
        rv() + bf(3, b"n" * 32),
        bf(1, b"attest") + vf(2, 1),
        rv(1, bf(1, b"chip")),
        rv(1, bf(1, b"chip") + bf(2, b"cert") + bf(1, b"duplicate")),
        rv(1, bf(1, b"chip") + vf(2, 1)),
        rv() + vf(5, 1),
        b"x" * 8193,
    ],
)
def test_classifier_refuses_unknown_ambiguous_or_malformed(payload):
    with pytest.raises(ProtocolMismatch) as caught:
        classify_payload(payload)
    assert caught.value.detail.retry_class == "never"


class Responder:
    """Synchronous in-process peer using the pinned mature Noise library."""

    peer_address = PEER

    def __init__(self, transform=None, wire_transform=None, before_reply=None):
        self.party = NoiseParty("responder")
        self.transform = transform or standard
        self.wire_transform = wire_transform or (lambda data: data)
        self.before_reply = before_reply
        self.sent = []
        self.incoming = []
        self.frames = []
        self.msg3_payload = None
        self.closed = False
        self.nonce = None

    def send(self, data):
        self.sent.append(data)
        if len(self.sent) == 1:
            challenge = list(_iter_fields(self.party.read(data)))
            assert len(challenge) == 1 and challenge[0][:2] == (1, 2)
            self.nonce = challenge[0][2]
            assert len(self.nonce) == 32
            key = self.party.conn.noise_protocol.handshake_state.s.public_bytes
            reply = self.party.write(self.transform(key, self.nonce))
            self.incoming.append(self.wire_transform(reply))
        elif len(self.sent) == 2:
            self.msg3_payload = self.party.read(data)
        else:
            _service, frame = decode_client_frame(self.party.decrypt(data))
            self.frames.append(frame)
            self.incoming.append(
                self.party.encrypt(
                    encode_server_frame(
                        ServiceFrame(
                            frame.stream_id,
                            "response",
                            response=ApplicationResponse(200, [], b'{"ok":true}', True),
                        )
                    )
                )
            )

    def recv(self, timeout):
        if self.before_reply:
            callback, self.before_reply = self.before_reply, None
            callback()
        return self.incoming.pop(0) if self.incoming else None

    def close(self):
        self.closed = True


class Connecting:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.peers = []
        self.urls = []

    def connect(self, url, *, checked, timeout):
        self.urls.append(url)
        peer = Responder(**self.kwargs)
        self.peers.append(peer)
        return peer


def test_standard_full_handshake_split_query_and_identity():
    token = "s0:" + ".".join(["syntheticA", "syntheticB", "syntheticC"])
    notary = ".".join(["opaque" + str(i) for i in range(6)])
    http = FakeHttp(
        {"/api/hatch/token": response({"token": token, "notary_token": " " + notary + " "})}
    )
    connector = Connecting()
    transport, http, guard, _ = build(handler=ok_frames([]), connector=connector, http=http)
    with transport:
        assert transport.sessions_list().status == 200
        assert transport.chat_history().status == 200
    peer = connector.peers[0]
    assert len(peer.sent[0]) == 66 and len(peer.sent[1]) == 64
    assert peer.msg3_payload == b"" and peer.party.finished and peer.closed
    assert len(peer.frames) == 2
    assert all(
        Header("accept-encoding", "identity") in frame.request.headers for frame in peer.frames
    )
    query = parse_qs(urlsplit(connector.urls[0]).query)
    assert set(query) == {"vm_id", "auth_token", "notary_token", "app_id", "request_id"}
    assert query["auth_token"] == [token] and query["notary_token"] == [notary]
    assert query["app_id"] == ["hatch-web"] and UUID(query["request_id"][0]).version == 4
    assert [urlsplit(call["url"]).path for call in http.calls] == [
        "/api/auth/check",
        "/api/session",
        "/api/hatch/token",
        "/api/hatch/vm/wake",
    ]
    assert http.calls[1]["url"].endswith("?vm_state=live")
    assert json.loads(http.calls[2]["body"]) == {
        "vmAddress": "wss://vm1.metaaivm.com/",
        "vmName": "vm1",
    }
    assert json.loads(http.calls[3]["body"]) == {"vm_id": "vm1", "retry_count": 0}
    assert len(guard.peers) == 5


@pytest.mark.parametrize(
    "transform",
    [
        lambda key, nonce: standard(b"w" * 32, nonce),
        lambda key, nonce: standard(key, b"w" * 32),
        lambda key, nonce: bf(2, bf(1, key)),
        lambda key, nonce: standard(key, b"r" * 32),
        lambda key, nonce: rv(),
    ],
)
def test_wrong_key_nonce_omission_and_rv_stop_before_msg3(transform):
    connector = Connecting(transform=transform)
    transport, _, _, _ = build(handler=ok_frames([]), connector=connector)
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert caught.value.detail.retry_class == "never"
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    peer = connector.peers[0]
    assert len(peer.sent) == 1 and not peer.frames and peer.closed
    if transform(b"k" * 32, b"n" * 32) == rv():
        with transport._trust.store.connection() as conn:
            assert (
                conn.execute("SELECT confidential_required FROM muse_trust_vms").fetchone()[0] == 1
            )


def test_nonce_replayed_from_an_actual_previous_attempt():
    connector = Connecting()
    transport, _, _, _ = build(handler=ok_frames([]), connector=connector)
    with transport:
        transport.sessions_list()
    old = connector.peers[0].nonce
    connector.kwargs["transform"] = lambda key, nonce: standard(key, old)
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert connector.peers[1].nonce != old and len(connector.peers[1].sent) == 1


LOW_ORDER = [
    bytes(32),
    b"\x01" + bytes(31),
    bytes.fromhex("e0eb7a7c3b41b8ae1656e3faf19fc46ada098deb9c32b1fd866205165f49b800"),
    bytes.fromhex("5f9c95bca3508c24b1d0b1559c83ef5b04445cc4581c8e86d8224eddd09f1157"),
    bytes.fromhex("ec" + "ff" * 30 + "7f"),
    bytes.fromhex("ed" + "ff" * 30 + "7f"),
    bytes.fromhex("ee" + "ff" * 30 + "7f"),
]


@pytest.mark.parametrize("key", LOW_ORDER)
def test_mature_library_refuses_all_seven_low_order_ephemerals(key):
    connector = Connecting(wire_transform=lambda wire: key + wire[32:])
    transport, _, _, _ = build(handler=ok_frames([]), connector=connector)
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert len(connector.peers[0].sent) == 1 and connector.peers[0].closed


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda wire: wire[:-1] + bytes([wire[-1] ^ 1]), ErrorCode.PROTOCOL_MISMATCH),
        (lambda wire: b"x" * 8290, ErrorCode.PROTOCOL_MISMATCH),
        (lambda wire: b"", ErrorCode.NETWORK_TIMEOUT),
    ],
)
def test_bad_aead_oversize_timeout_close_without_msg3(mutate, code):
    connector = Connecting(wire_transform=mutate)
    transport, _, _, _ = build(handler=ok_frames([]), connector=connector)
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == code
    assert len(connector.peers[0].sent) == 1 and connector.peers[0].closed


def test_noise_frame_budgets_do_not_rewind():
    initiator, responder = finished_pair()
    initiator._sent_frames = 2**32
    with pytest.raises(ProtocolMismatch):
        initiator.encrypt(b"x")
    responder._received_frames = 2**32
    with pytest.raises(ProtocolMismatch):
        responder.decrypt(b"x")
    assert initiator._sent_frames == responder._received_frames == 2**32


def response(document, status=200):
    from metamuse._muse_http import HttpResponse

    return HttpResponse(status, canonical_json(document), {}, PEER)


def assignment(**changes):
    row = {
        "status": "assigned",
        "vm_id": "vm1",
        "vm_name": "vm1",
        "endpoint_url": "wss://vm1.metaaivm.com/",
        "vm_type": "hatch_vm",
        "vm_state": "RUNNING",
    }
    row.update(changes)
    return row


@pytest.mark.parametrize(
    "changes,code,sticky",
    [
        ({"vm_state": "DISABLED"}, ErrorCode.RESOURCE_UNAVAILABLE, False),
        ({"vm_state": "DELETING"}, ErrorCode.RESOURCE_UNAVAILABLE, False),
        ({"vm_state": "ERROR"}, ErrorCode.RESOURCE_UNAVAILABLE, False),
        ({"vm_state": "CVM_REPLACING"}, ErrorCode.PROTOCOL_MISMATCH, True),
        ({"vm_type": "hatch_cvm"}, ErrorCode.PROTOCOL_MISMATCH, True),
        ({"vm_type": "hatch_cvm", "endpoint_url": "invalid"}, ErrorCode.PROTOCOL_MISMATCH, True),
        ({"vm_type": None}, ErrorCode.PROTOCOL_MISMATCH, False),
        ({"vm_name": "other"}, ErrorCode.PROTOCOL_MISMATCH, False),
        ({"endpoint_url": "wss://other.metaaivm.com/"}, ErrorCode.PROTOCOL_MISMATCH, False),
        ({"replace_attempt_id": "pending"}, ErrorCode.PROTOCOL_MISMATCH, False),
        ({"provisioning_pins": ["synthetic"]}, ErrorCode.PROTOCOL_MISMATCH, False),
        ({"vms": []}, ErrorCode.PROTOCOL_MISMATCH, False),
        ({"vms": [assignment(), assignment()]}, ErrorCode.PROTOCOL_MISMATCH, False),
        ({"vms": [assignment(vm_type="hatch_cvm")]}, ErrorCode.PROTOCOL_MISMATCH, True),
        ({"vm_type": "hatch_cvm", "vms": [assignment()]}, ErrorCode.PROTOCOL_MISMATCH, True),
    ],
)
def test_assignment_refusals_never_wake_or_dial(changes, code, sticky):
    http = FakeHttp({"/api/session": response(assignment(**changes))})
    connector = Connecting()
    transport, http, _, _ = build(handler=ok_frames([]), connector=connector, http=http)
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == code
    assert len(http.calls) == 2 and not connector.urls
    with transport._trust.store.connection() as conn:
        rows = conn.execute("SELECT confidential_required FROM muse_trust_vms").fetchall()
    assert any(row[0] for row in rows) == sticky


@pytest.mark.parametrize("token", ["s0:a.b.c", "legacy-token"])
def test_hatch_supported_opaque_tokens(token):
    connector = Connecting()
    transport, _, _, _ = build(
        handler=ok_frames([]),
        connector=connector,
        http=FakeHttp({"/api/hatch/token": response({"token": token})}),
    )
    with transport:
        transport.sessions_list()
    assert parse_qs(urlsplit(connector.urls[0]).query)["auth_token"] == [token]


@pytest.mark.parametrize(
    "token",
    ["s0:a.b", "s0:a..c", "s0:a.b.c:d", "legacy:token", "s0:a.b.c\n", "x" * 4097, "s0:α.b.c"],
)
def test_hatch_malformed_tokens_never_wake_or_dial(token):
    connector = Connecting()
    transport, http, _, _ = build(
        handler=ok_frames([]),
        connector=connector,
        http=FakeHttp({"/api/hatch/token": response({"token": token})}),
    )
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == (ErrorCode.PROTOCOL_MISMATCH)
    assert len(http.calls) == 3 and not connector.urls


@pytest.mark.parametrize(
    "notary", ["delegation.synthetic", "a.b.c", "a.b.c.d.e.f\n", 3, "x" * 4097]
)
def test_unsupported_notary_is_not_silently_dropped(notary):
    connector = Connecting()
    transport, http, _, _ = build(
        handler=ok_frames([]),
        connector=connector,
        http=FakeHttp({"/api/hatch/token": response({"token": "legacy", "notary_token": notary})}),
    )
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert len(http.calls) == 3 and not connector.urls


class Clock:
    def __init__(self):
        self.now = 0.0
        self.delays = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.delays.append(seconds)
        self.now += seconds


class Redial(Connecting):
    def __init__(self, statuses, clock, elapsed=0):
        super().__init__()
        self.statuses = list(statuses)
        self.clock = clock
        self.elapsed = elapsed
        self.attempts = 0

    def connect(self, url, *, checked, timeout):
        self.attempts += 1
        self.clock.now += min(self.elapsed, timeout)
        if self.statuses:
            raise UpgradeFailure(self.statuses.pop(0))
        return super().connect(url, checked=checked, timeout=timeout)


@pytest.mark.parametrize("count", [0, 3, 20])
def test_bounded_wake_redial_schedule(count):
    clock = Clock()
    connector = Redial([502, 503, 504] * count, clock)
    transport, http, _, _ = build(
        handler=ok_frames([]), connector=connector, clock=clock, sleep=clock.sleep
    )
    if count == 20:
        with pytest.raises(CoreError) as caught:
            transport.sessions_list()
        assert caught.value.detail.code == ErrorCode.RESOURCE_UNAVAILABLE
        assert "not running yet" in caught.value.detail.next_action
        redials = 20
    else:
        with transport:
            transport.sessions_list()
        redials = count * 3
    wakes = [
        json.loads(call["body"])
        for call in http.calls
        if urlsplit(call["url"]).path == "/api/hatch/vm/wake"
    ]
    assert connector.attempts == 1 + redials
    assert len(wakes) == 1 + redials
    assert wakes[0] == {"vm_id": "vm1", "retry_count": 0}
    ids = []
    for n, wake in enumerate(wakes[1:], 1):
        assert wake["retry_count"] == n and wake["vm_id"] == "vm1"
        assert UUID(wake["connect_attempt_id"]).version == 4
        ids.append(wake["connect_attempt_id"])
    assert len(set(ids)) == len(ids)
    assert clock.delays == [max(2.0, min(0.2 * 1.5 ** (n - 1), 8.0)) for n in range(1, redials + 1)]
    assert all(
        urlsplit(call["url"]).path
        in {"/api/auth/check", "/api/session", "/api/hatch/token", "/api/hatch/vm/wake"}
        for call in http.calls
    )


def test_redial_stops_at_240_seconds():
    clock = Clock()
    connector = Redial([503] * 50, clock, elapsed=20)
    transport, _, _, _ = build(
        handler=ok_frames([]), connector=connector, clock=clock, sleep=clock.sleep
    )
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.RESOURCE_UNAVAILABLE
    assert clock.now <= 240 and connector.attempts < 21


@pytest.mark.parametrize(
    "status,code",
    [
        (401, ErrorCode.AUTH_REQUIRED),
        (403, ErrorCode.AUTH_REQUIRED),
        (429, ErrorCode.RATE_LIMITED),
        (500, ErrorCode.RESOURCE_UNAVAILABLE),
    ],
)
def test_non_redial_upgrade_failures_map_without_retry(status, code):
    clock = Clock()
    connector = Redial([status], clock)
    transport, http, _, _ = build(
        handler=ok_frames([]), connector=connector, clock=clock, sleep=clock.sleep
    )
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == code and connector.attempts == 1
    assert len(http.calls) == 4 and not clock.delays
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "status,code",
    [
        (401, ErrorCode.AUTH_REQUIRED),
        (403, ErrorCode.AUTH_REQUIRED),
        (429, ErrorCode.RATE_LIMITED),
        (502, ErrorCode.RESOURCE_UNAVAILABLE),
        (503, ErrorCode.RESOURCE_UNAVAILABLE),
        (504, ErrorCode.RESOURCE_UNAVAILABLE),
        (0, ErrorCode.NETWORK_TIMEOUT),
    ],
)
def test_actual_curl_failure_mapping_keeps_no_raw_exception(monkeypatch, status, code):
    from curl_cffi.const import CurlInfo, CurlOpt

    class WebSocket:
        def __init__(self):
            self.curl = self
            self.closed = False

        def connect(self, url, **kwargs):
            assert kwargs["headers"] == {"Origin": "https://muse.ai"}
            raise RuntimeError(f"{url} synthetic-private-response-body")

        def getinfo(self, info):
            assert info == CurlInfo.RESPONSE_CODE
            return status

        def close(self):
            self.closed = True

    ws = WebSocket()
    monkeypatch.setattr("metamuse._muse_http.load_curl", lambda: (None, lambda: ws, CurlOpt))
    url = "wss://hatch.metaaivm.com/v1/noise?auth_token=synthetic-sensitive"
    with pytest.raises(CoreError) as caught:
        CurlConnector().connect(url, checked=checked_url(url), timeout=20)
    assert caught.value.detail.code == code and ws.closed
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert url not in str(caught.value) and "synthetic-private" not in str(caught.value)


def test_resolve_entry_and_option_dict_preserve_only_checked_addresses():
    from curl_cffi.const import CurlOpt

    addresses = ("2606:4700:4700::1111", "8.8.8.8", "1.1.1.1")
    checked = checked_url("https://muse.ai/api/auth/check", addresses)
    expected = ["muse.ai:443:[2606:4700:4700::1111],8.8.8.8,1.1.1.1"]
    assert resolve_entries(checked) == expected
    options = _options(CurlOpt, checked)
    assert options[CurlOpt.RESOLVE] == expected
    assert options[CurlOpt.SSL_VERIFYPEER] == 1 and options[CurlOpt.SSL_VERIFYHOST] == 2
    assert options[CurlOpt.PROXY] == b""
    assert "9.9.9.9" not in str(options[CurlOpt.RESOLVE])


@pytest.mark.parametrize("encode", [lambda text: text, lambda text: quote(text, safe="")])
def test_redaction_raw_and_encoded_interpolated_sentinels(encode):
    suffix = "sentinel" + "Synthetic42"
    s0 = "s0:" + ".".join([suffix + str(i) for i in range(3)])
    notary = ".".join([suffix + str(i) for i in range(6)])
    attempt = "connect-" + suffix
    text = f"wss://example.test/?notary_token={notary}&auth_token={s0}&connect_attempt_id={attempt}"
    rendered = sanitize_text(encode(text))
    assert suffix not in rendered and quote(s0, safe="") not in rendered
    document = {
        "session_id": "routing-identity",
        "unknown": [encode(s0), encode(notary)],
        "notaryToken": notary,
        "wake_body": {"vm_id": "vm1", "retry_count": 1, "connect_attempt_id": attempt},
    }
    rendered = json.dumps(sanitize_document(document))
    assert suffix not in rendered and "routing-identity" in rendered
    assert sanitize_document({"vm_id": "vm1", "retry_count": 0}) == "[REDACTED]"


def test_guard_refuses_extra_endpoints_and_query_injection():
    guard = MuseURLGuard(lambda host: ("8.8.8.8",))
    for url, purpose in [
        ("https://muse.ai/api/hatch/lease-vm", "hatch.wake"),
        ("https://muse.ai/api/hatch/vm/wake?token=x", "hatch.wake"),
        ("https://muse.ai/api/session", "session"),
        ("wss://hatch.metaaivm.com/v1/noise?vm_id=x&auth_token=x", "wss.noise"),
    ]:
        with pytest.raises(CoreError):
            guard.check(url, purpose=purpose)


def test_stale_mint_remints_only_once_without_wake():
    hint = (
        base64.urlsafe_b64encode(canonical_json({"restrictions": ["timeout:1"]}))
        .decode()
        .rstrip("=")
    )
    token = ".".join(["one", "two", hint, "four", "five", "six"])
    http = FakeHttp({"/api/hatch/token": response({"token": "legacy", "notary_token": token})})
    transport, http, _, connector = build(handler=ok_frames([]), http=http, wall_clock=lambda: 100)
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.RESOURCE_UNAVAILABLE
    assert len(http.calls) == 4 and not connector.urls


def test_cas_race_stops_before_msg3():
    connector = Connecting()
    transport, _, _, _ = build(handler=ok_frames([]), connector=connector)
    store = transport._trust.store
    connector.kwargs["before_reply"] = lambda: store.muse_freeze(
        provider="synthetic",
        account="synthetic",
        vm_id="vm1",
        viewer_id="viewer-fixture",
        session_binding_id="new-session",
    )
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.STORAGE
    assert len(connector.peers[0].sent) == 1 and connector.peers[0].closed


def test_original_uncertain_send_remains_uncertain_on_later_reconnect_failure():
    # Existing B10 fixtures separately verify durable reconciliation and no refund.
    # Here the same connection's original handoff cannot be reclassified by a later auth stop.
    from test_muse_transport import FakeConnector

    from metamuse.muse import SEND_UNCERTAIN, Exchange

    frames = []

    def handler(frame):
        frames.append(frame)
        return []

    connector = FakeConnector(handler)
    transport, http, _, _ = build(handler=handler, connector=connector)
    with transport:
        attempt = transport.chat_stream(
            mutation("chat.stream", {"text": "question"}),
            authorization=Token(),
            timeout_s=0,
            exchange=Exchange(
                session_id="sess-a", baseline_seq=0, sent_text="question", request_marker="req-1"
            ),
        )
    http.responses["/api/auth/check"] = response({}, 401)
    with pytest.raises(CoreError):
        transport.chat_history(session_id="sess-a")
    connector.thread.join(2)
    assert attempt.state == SEND_UNCERTAIN and attempt.bytes_handed_off
    assert len(frames) == 1 and len(connector.urls) == 1


@pytest.mark.parametrize("encode", [lambda value: value, lambda value: quote(value, safe="")])
def test_raw_and_encoded_wake_body_text_is_sanitized(encode):
    sentinel = "attempt-" + "SyntheticSentinel42"
    body = canonical_json(
        {"vm_id": "vm1", "retry_count": 1, "connect_attempt_id": sentinel}
    ).decode()
    assert sanitize_text(encode(body)) == "[REDACTED]"
    assert sentinel not in sanitize_text(encode(json.dumps({"connect_attempt_id": sentinel})))


@pytest.mark.parametrize(
    "change",
    [{"ok": False}, {"viewer_id": None}, {"session_binding_id": None}, {"viewer_id": "bad\nowner"}],
)
def test_authenticated_owner_and_session_are_required_before_assignment(change):
    auth = {
        "ok": True,
        "access_token": "legacy",
        "viewer_id": "owner",
        "session_binding_id": "session",
    }
    auth.update(change)
    connector = Connecting()
    transport, http, _, _ = build(
        handler=ok_frames([]),
        connector=connector,
        http=FakeHttp({"/api/auth/check": response(auth)}),
    )
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.AUTH_REQUIRED
    assert len(http.calls) == 1 and not connector.urls


def test_vm_row_selection_allows_other_rows_but_requires_consistency():
    row = assignment()
    row.pop("status")
    row["name"] = row.pop("vm_name")
    other = {**row, "vm_id": "vm2", "name": "vm2", "endpoint_url": "wss://vm2.metaaivm.com/"}
    connector = Connecting()
    transport, _, _, _ = build(
        handler=ok_frames([]),
        connector=connector,
        http=FakeHttp(
            {"/api/session": response({"status": "assigned", "vm_id": "vm1", "vms": [other, row]})}
        ),
    )
    with transport:
        transport.sessions_list()
    assert len(connector.peers[0].frames) == 1


def test_auth_ref_rotation_cannot_erase_confidential_history():
    connector = Connecting(transform=lambda key, nonce: rv())
    transport, _, _, _ = build(handler=ok_frames([]), connector=connector)
    with pytest.raises(CoreError):
        transport.sessions_list()
    store = transport._trust.store
    again, http, _, _ = build(
        handler=ok_frames([]), connector=Connecting(), store=store, auth_ref="rotated-auth-ref"
    )
    with pytest.raises(CoreError) as caught:
        again.sessions_list()
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert len(http.calls) == 2


def test_generation_change_closes_connected_transport_before_another_app_frame():
    connector = Connecting()
    transport, _, _, _ = build(handler=ok_frames([]), connector=connector)
    transport.sessions_list()
    store = transport._trust.store
    store.muse_freeze(
        provider="synthetic",
        account="synthetic",
        vm_id="vm1",
        viewer_id="viewer-fixture",
        session_binding_id="changed-session",
    )
    assert connector.peers[0].closed
    with pytest.raises(CoreError) as caught:
        transport.chat_history()
    assert caught.value.detail.code == ErrorCode.STORAGE
    assert len(connector.peers[0].frames) == 1


@pytest.mark.parametrize("encoding", [[], [Header("content-encoding", "identity")]])
def test_identity_response_encoding_succeeds(encoding):
    from metamuse._muse_link import ReceiveLoop, ScriptedLink

    link = ScriptedLink()
    with ReceiveLoop(link) as loop:
        stream = loop.open_request(verb="GET", path="/chat/history")
        link.push(
            encode_server_frame(
                ServiceFrame(
                    stream, "response", response=ApplicationResponse(200, encoding, b"{}", True)
                )
            )
        )
        assert loop.collect(stream) == (200, b"{}")


@pytest.mark.parametrize(
    "encoding",
    [
        [Header("content-encoding", "zstd")],
        [Header("content-encoding", "gzip")],
        [Header("content-encoding", "identity"), Header("Content-Encoding", "identity")],
    ],
)
def test_unsupported_or_duplicate_encoding_never_promotes_body(encoding):
    from metamuse._muse_link import ReceiveLoop, ScriptedLink

    link = ScriptedLink()
    with ReceiveLoop(link) as loop:
        stream = loop.open_request(verb="GET", path="/chat/history")
        link.push(
            encode_server_frame(
                ServiceFrame(
                    stream, "response", response=ApplicationResponse(200, encoding, b"{}", True)
                )
            )
        )
        with pytest.raises(ProtocolMismatch):
            loop.collect(stream)
        assert loop.failed


def test_connection_lifetime_known_credentials_are_redacted_on_every_call():
    from test_muse_transport import SECRET, FakeConnector

    from metamuse._muse_link import response_frame

    def handler(frame):
        body = json.dumps({"unknown": SECRET}).encode()
        return [response_frame(frame.stream_id, 200, body, end_body=True)]

    connector = FakeConnector(handler)
    transport, _, _, _ = build(handler=handler, connector=connector)
    with transport:
        for _ in range(2):
            assert SECRET.encode() not in transport.sessions_list().body
    connector.thread.join(2)
    assert transport._connection_secrets == ()


def test_cleanup_exception_cannot_leak_credentials(monkeypatch):
    sentinel = "s0:" + ".".join(["privateSynthetic"] * 3)

    def fail_close(self):
        raise RuntimeError("cleanup " + sentinel)

    monkeypatch.setattr(Responder, "close", fail_close)
    connector = Connecting(transform=lambda key, nonce: standard(key, b"w" * 32))
    transport, _, _, _ = build(handler=ok_frames([]), connector=connector)
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert caught.value.__context__ is None and sentinel not in str(caught.value)


def test_missing_mature_responder_key_api_blocks_as_dependency(monkeypatch):
    def unavailable(self):
        raise CoreError(ErrorCode.DEPENDENCY_MISSING, "synthetic missing API")

    monkeypatch.setattr(NoiseParty, "responder_static", unavailable)
    connector = Connecting()
    transport, _, _, _ = build(handler=ok_frames([]), connector=connector)
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.DEPENDENCY_MISSING
    assert len(connector.peers[0].sent) == 1 and connector.peers[0].closed


@pytest.mark.parametrize(
    "text", ["source %2F literal", "https://example.test/items?q=%E4%B8%AD", "a%20b"]
)
def test_redaction_preserves_nonsecret_percent_encoded_source_text(text):
    assert sanitize_text(text) == text
    assert sanitize_document({"content": text}) == {"content": text}


# Round-2 independent review probes, converted to permanent safety assertions.
@pytest.mark.parametrize(
    "payload",
    [
        assignment(vm_type="hatch_cvm", vm_state="CVM_RUNNING", vms=[]),
        assignment(status="provisioning", vm_type="hatch_cvm", vm_state="CVM_PROVISIONING"),
        assignment(cvm_required=True),
        assignment(cvmReplacementEnabled=True),
        assignment(vms=[assignment(), assignment(vm_id=" VM1 ", vm_type="hatch_cvm")]),
        assignment(vms=[assignment(vm_id="other", vm_type="hatch_cvm")]),
        {"status": "unavailable", "vm_id": None, "cvm_required": True},
    ],
)
def test_r2_f2_any_authenticated_cvm_hint_persists_before_selection(payload):
    http = FakeHttp({"/api/session": response(payload)})
    connector = Connecting()
    transport, _, _, _ = build(handler=ok_frames([]), http=http, connector=connector)
    with pytest.raises(CoreError) as exc:
        transport.sessions_list()
    assert exc.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    with transport._trust.store.connection() as conn:
        assert (
            conn.execute("SELECT confidential_required FROM muse_trust_accounts").fetchone()[0] == 1
        )
    http.responses["/api/session"] = response(assignment())
    with pytest.raises(CoreError) as exc:
        transport.sessions_list()
    assert exc.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert not connector.urls
    assert len(http.calls) == 4  # auth/session twice; no mint/wake


@pytest.mark.parametrize(
    "key", ["token", "notary_token", "notaryToken", "noiseNotaryToken", "endorsement"]
)
@pytest.mark.parametrize(
    "value",
    [
        "delegation.aa.bb.cc.dd.ee",
        "Delegation.aa.bb.cc.dd.ee",
        "endorsement.aa.bb.cc.dd.ee",
        "Endorsement.aa.bb.cc.dd.ee",
        "s0:malformed",
    ],
)
def test_r2_f3_every_credential_field_refuses_unsupported_shapes(key, value):
    connector = Connecting()
    transport, http, _, _ = build(
        handler=ok_frames([]),
        connector=connector,
        http=FakeHttp({"/api/hatch/token": response({"token": "synthetic", key: value})}),
    )
    with pytest.raises(CoreError) as exc:
        transport.sessions_list()
    assert exc.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert exc.value.detail.retry_class == "never"
    assert not connector.urls
    assert not any("/wake" in call["url"] for call in http.calls)


@pytest.mark.parametrize(
    "payload",
    [
        b"\x88\x00\x00" + standard(),
        b"\x08\x80\x00" + standard(),
        b"\x92\x00\x44" + bf(1, b"k" * 32) + bf(2, b"n" * 32),
        b"\x12\xc4\x00" + bf(1, b"k" * 32) + bf(2, b"n" * 32),
        bf(2, b"\x8a\x00\x20" + b"k" * 32 + bf(2, b"n" * 32)),
        b"\x08" + b"\x80" * 9 + b"\x02" + standard(),
        bf(1, b"") + vf(2, 1) + bf(3, b"n" * 32),
    ],
)
def test_r2_f4_handshake_varints_are_minimal_uint64_and_rv_attestation_nonempty(payload):
    with pytest.raises(ProtocolMismatch):
        classify_payload(payload)


@pytest.mark.parametrize(
    "payload",
    [
        rv(3),
        rv(1, bf(1, b"only-one-chip-field")),
        bf(1, b"") + vf(2, 1),
        vf(2, 1) + b"\x1a\x80",
        rv() + bf(2, b"ambiguous"),
    ],
)
def test_r2_f4_malformed_rv_shape_is_permanent_confidentiality(payload):
    connector = Connecting(transform=lambda key, nonce: payload)
    transport, _, _, _ = build(handler=ok_frames([]), connector=connector)
    with pytest.raises(CoreError) as exc:
        transport.sessions_list()
    assert exc.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert len(connector.peers[0].sent) == 1
    with transport._trust.store.connection() as conn:
        assert (
            conn.execute("SELECT confidential_required FROM muse_trust_accounts").fetchone()[0] == 1
        )
        assert conn.execute("SELECT confidential_required FROM muse_trust_vms").fetchone()[0] == 1
    connector.kwargs.clear()
    with pytest.raises(CoreError) as exc:
        transport.sessions_list()
    assert exc.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert len(connector.peers) == 1


@pytest.mark.parametrize(
    "key",
    [
        "nonce",
        "client_nonce",
        "proof",
        "recovery_key",
        "rv_key",
        "endorsement",
        "notaryToken",
        "noiseNotaryToken",
        "connectAttemptId",
        "connect_attempt_id",
        "wakeBody",
        "handshake",
    ],
)
@pytest.mark.parametrize("shape", ["query", "json"])
def test_r2_f5_query_and_raw_json_aliases_scrub(key, shape):
    sentinel = "synthetic-value-for-review-42"
    value = (
        f"wss://example.invalid/?{key}={sentinel}"
        if shape == "query"
        else f'prefix {{"{key}": "{sentinel}"'
    )
    assert sentinel not in sanitize_text(value)
    assert sentinel not in sanitize_text(quote(value, safe=""))


def test_r2_f5_embedded_wake_body_scrubbed():
    sentinel = "synthetic-vm-review-42"
    assert sentinel not in sanitize_text(
        f'prefix POST wake {{"vm_id":"{sentinel}","retry_count":0}} suffix'
    )


def test_r2_f5_dataclass_reprs_hide_binding_headers_and_body():
    from dataclasses import is_dataclass

    from metamuse._muse_adapters import BoundMuseCredentials
    from metamuse._muse_codec import ApplicationRequest, BodyChunk, NoiseTransportFrame, Reset
    from metamuse._muse_http import HttpResponse
    from metamuse.models import MuseBinding

    sentinel = "synthetic-review-secret-42"
    samples = [
        HttpResponse(200, sentinel.encode(), {"Set-Cookie": sentinel}, PEER),
        BoundMuseCredentials(
            MuseBinding(provider="synthetic", account="account", auth_ref="auth_" + "a" * 32)
        ),
        Header("Cookie", sentinel),
        ApplicationRequest("POST", "/safe", [Header("Cookie", sentinel)], sentinel.encode()),
        ApplicationResponse(200, [Header("X", sentinel)], sentinel.encode()),
        BodyChunk(sentinel.encode()),
        Reset(1, sentinel),
        NoiseTransportFrame(1, 0, 1, sentinel.encode()),
        mutation("session.start", {"title": sentinel}),
    ]
    for sample in samples:
        assert is_dataclass(sample)
        assert sentinel not in repr(sample)
        assert "auth_" + "a" * 32 not in repr(sample)


def test_r2_f5_connection_remembers_nonce_encodings_and_request_id():
    class EchoPeer(Responder):
        def send(self, data):
            if len(self.sent) < 2:
                return super().send(data)
            self.sent.append(data)
            _, frame = decode_client_frame(self.party.decrypt(data))
            values = [
                self.nonce.hex(),
                base64.b64encode(self.nonce).decode(),
                base64.urlsafe_b64encode(self.nonce).decode(),
                base64.urlsafe_b64encode(self.nonce).decode().rstrip("="),
                self.request_id,
            ]
            self.values = values
            self.incoming.append(
                self.party.encrypt(
                    encode_server_frame(
                        ServiceFrame(
                            frame.stream_id,
                            "response",
                            response=ApplicationResponse(
                                200, [], canonical_json({"unknown": values}), True
                            ),
                        )
                    )
                )
            )

    class EchoConnecting(Connecting):
        def connect(self, url, *, checked, timeout):
            self.urls.append(url)
            peer = EchoPeer()
            peer.request_id = parse_qs(urlsplit(url).query)["request_id"][0]
            self.peers.append(peer)
            return peer

    connector = EchoConnecting()
    transport, _, _, _ = build(handler=ok_frames([]), connector=connector)
    with transport:
        for _ in range(2):
            result = transport.sessions_list()
            for value in connector.peers[0].values:
                assert value.encode() not in result.body


@pytest.mark.parametrize("phase", ["wake", "dial", "handshake"])
def test_r2_f6_deadline_exhaustion_never_reaches_next_io(phase):
    from test_muse_transport import Guard

    now = [0.0]

    class ExpiringGuard(Guard):
        def check(self, url, *, purpose):
            checked = super().check(url, purpose=purpose)
            if (phase == "wake" and purpose == "hatch.wake") or (
                phase == "dial" and purpose == "wss.noise"
            ):
                now[0] += 250
            return checked

        def verify_peer(self, checked, peer_address):
            super().verify_peer(checked, peer_address)
            if phase == "handshake" and checked.url.startswith("wss://"):
                now[0] += 250

    connector = Connecting()
    transport, http, _, _ = build(
        handler=ok_frames([]), guard=ExpiringGuard(), connector=connector, clock=lambda: now[0]
    )
    with pytest.raises(CoreError) as exc:
        transport.sessions_list()
    assert exc.value.detail.code == ErrorCode.RESOURCE_UNAVAILABLE
    assert all(call["timeout"] > 0 for call in http.calls)
    if phase == "wake":
        assert not any("/wake" in call["url"] for call in http.calls)
    if phase != "handshake":
        assert not connector.urls
    else:
        assert not connector.peers[0].sent


@pytest.mark.parametrize("status", [202, 204])
def test_r2_f6_wake_accepts_authorized_success_statuses(status):
    transport, _, _, _ = build(
        handler=ok_frames([]),
        connector=Connecting(),
        http=FakeHttp({"/api/hatch/vm/wake": response({}, status)}),
    )
    with transport:
        assert transport.sessions_list().status == 200


@pytest.mark.parametrize(
    "path,status,code",
    [
        ("/api/session", 404, ErrorCode.RESOURCE_UNAVAILABLE),
        ("/api/session", 409, ErrorCode.RESOURCE_UNAVAILABLE),
        ("/api/session", 410, ErrorCode.RESOURCE_UNAVAILABLE),
        ("/api/hatch/vm/wake", 409, ErrorCode.RESOURCE_UNAVAILABLE),
        ("/api/hatch/token", 202, ErrorCode.PROTOCOL_MISMATCH),
        ("/api/auth/check", 204, ErrorCode.PROTOCOL_MISMATCH),
        ("/api/session", 408, ErrorCode.PROTOCOL_MISMATCH),
    ],
)
def test_r2_f6_unexpected_http_status_is_not_auth(path, status, code):
    transport, _, _, _ = build(handler=ok_frames([]), http=FakeHttp({path: response({}, status)}))
    with pytest.raises(CoreError) as exc:
        transport.sessions_list()
    assert exc.value.detail.code == code


def _r2_external_session_change(store):
    import sqlite3

    with sqlite3.connect(store.paths.database) as conn:
        conn.execute(
            "UPDATE muse_trust_accounts SET session_hash=?, generation=generation+1", ("a" * 64,)
        )


@pytest.mark.parametrize("phase", ["before_mint", "before_dial", "before_retry_wake", "before_app"])
def test_r2_f8_each_new_connection_currentness_check_stops_next_effect(phase, monkeypatch):
    connector = Connecting()
    transport, http, _, _ = build(
        handler=ok_frames([]), connector=connector, sleep=lambda value: None
    )
    store = transport._trust.store
    if phase == "before_mint":
        original = store.muse_freeze

        def freeze_then_change(**kwargs):
            snap = original(**kwargs)
            _r2_external_session_change(store)
            return snap

        monkeypatch.setattr(store, "muse_freeze", freeze_then_change)
    elif phase == "before_dial":
        original = http.request

        def wake_then_change(*args, **kwargs):
            result = original(*args, **kwargs)
            if "/wake" in args[1]:
                _r2_external_session_change(store)
            return result

        monkeypatch.setattr(http, "request", wake_then_change)
    elif phase == "before_retry_wake":

        def fail_then_change(*args, **kwargs):
            _r2_external_session_change(store)
            raise UpgradeFailure(503)

        monkeypatch.setattr(connector, "connect", fail_then_change)
    else:
        original = store.muse_watch

        def watch_then_change(*args):
            original(*args)
            _r2_external_session_change(store)

        monkeypatch.setattr(store, "muse_watch", watch_then_change)
    with pytest.raises(CoreError) as exc:
        transport.sessions_list()
    assert exc.value.detail.code == ErrorCode.STORAGE
    wakes = [call for call in http.calls if "/wake" in call["url"]]
    if phase == "before_mint":
        assert len(http.calls) == 2 and not connector.urls
    elif phase == "before_dial":
        assert not connector.urls
    elif phase == "before_retry_wake":
        assert len(wakes) == 1
    else:
        assert not connector.peers[0].frames and connector.peers[0].closed


def test_r2_f8_connected_cross_process_staleness_stops_before_app_frame():
    connector = Connecting()
    transport, _, _, _ = build(handler=ok_frames([]), connector=connector)
    with transport:
        transport.sessions_list()
        peer = connector.peers[0]
        _r2_external_session_change(transport._trust.store)
        assert not transport._trust_invalidated
        with pytest.raises(CoreError) as exc:
            transport.sessions_list()
        assert exc.value.detail.code == ErrorCode.STORAGE
        assert len(peer.frames) == 1 and peer.closed


def test_r2_f2_wire_id_exact_and_storage_id_normalized():
    http = FakeHttp({"/api/session": response(assignment(vm_id=" VM1 ", vm_name="VM1"))})
    connector = Connecting()
    transport, _, _, _ = build(handler=ok_frames([]), http=http, connector=connector)
    with transport:
        assert transport.sessions_list().status == 200
        assert transport._snapshot.vm_id == "vm1"
    assert parse_qs(urlsplit(connector.urls[0]).query)["vm_id"] == [" VM1 "]
    wake = next(json.loads(call["body"]) for call in http.calls if "/wake" in call["url"])
    assert wake["vm_id"] == " VM1 "
    mint = next(json.loads(call["body"]) for call in http.calls if "/token" in call["url"])
    assert mint == {"vmAddress": "wss://vm1.metaaivm.com/", "vmName": "VM1"}


@pytest.mark.parametrize("timeout", [0.0, -1.0])
def test_r2_f6_handshake_deadline_checked_before_msg1(timeout):
    from metamuse._muse_link import IncompleteBody, ScriptedLink
    from metamuse._muse_noise import handshake_standard

    link = ScriptedLink()
    with pytest.raises(IncompleteBody):
        handshake_standard(
            NoiseParty("initiator"),
            link,
            timeout=timeout,
            clock=lambda: 0.0,
            confidential=lambda: pytest.fail("confidential"),
            standard_handoff=lambda fn: pytest.fail("handoff"),
        )
    assert not link.sent


@pytest.mark.parametrize(
    "key",
    [
        "client-nonce",
        "clientNonce",
        "recovery-key",
        "recoveryKey",
        "rv-key",
        "rvKey",
        "noise-notary-token",
        "connect-attempt-id",
    ],
)
def test_r2_f5_all_key_styles_scrub(key):
    sentinel = "synthetic-review-secret-42"
    for value in (f"{key}={sentinel}", f'prefix {{"{key}":"{sentinel}"}}'):
        assert sentinel not in sanitize_text(value)


@pytest.mark.parametrize(
    "key", ["token", "notary_token", "notaryToken", "noiseNotaryToken", "endorsement"]
)
def test_r2_f3_oversized_s0_is_protocol_never(key):
    token = "s0:" + "a" * 4096 + ".b.c"
    transport, _, _, _ = build(
        handler=ok_frames([]),
        http=FakeHttp({"/api/hatch/token": response({"token": "synthetic", key: token})}),
    )
    with pytest.raises(CoreError) as exc:
        transport.sessions_list()
    assert exc.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert exc.value.detail.retry_class == "never"
