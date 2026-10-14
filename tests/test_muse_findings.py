"""Regressions named for the B09 adversarial review. Offline. No sockets.

Tests that touch curl_cffi forbid Curl.perform and DNS. A socket monkeypatch
does not stop native libcurl.
"""

from __future__ import annotations

import importlib.util
import json
import socket
import threading
import time
from pathlib import Path

import pytest

from metamuse._muse_codec import (
    ProtocolMismatch,
    decode_server_frame,
    decode_service_frame,
    encode_server_frame,
)
from metamuse._muse_http import (
    AUTH_RESPONSE_MAX_BYTES,
    AuthBodyBuffer,
    CurlSocket,
)
from metamuse._muse_link import (
    MAX_COLLECT_FRAMES,
    IncompleteBody,
    ReceiveLoop,
    ScriptedLink,
    SendUncertain,
    chunk_frame,
    response_frame,
)
from metamuse._muse_noise import NoiseParty
from metamuse.models import CoreError, ErrorCode
from metamuse.muse import (
    EVIDENCE_ACKED,
    EVIDENCE_APP_ERROR,
    EVIDENCE_UNDECIDED,
    FAILED_BEFORE_SEND,
    HATCH_WSS_BASE,
    MUSE_ORIGIN,
    PACKET_MAX_UTF8_BYTES,
    QUESTION_MAX_UTF8_BYTES,
    SEND_UNCERTAIN,
    SENT,
    Exchange,
    MuseProgrammingError,
    MuseProtocolStop,
    MuseStateUnknown,
    MuseTransport,
    attempt_evidence,
    prepare_muse_request,
    user_echo,
)
from metamuse.safety import CheckedURL, action_digest


def _helpers():
    path = Path(__file__).with_name("test_muse_transport.py")
    spec = importlib.util.spec_from_file_location("muse_transport_helpers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("muse transport helpers are missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


H = _helpers()
REVIEW = H.REVIEW_SECRET


def _no_chain(exc: BaseException) -> None:
    assert exc.__cause__ is None
    assert exc.__context__ is None
    assert REVIEW not in str(exc)
    assert "supersecret" not in str(exc)


def _review_http() -> object:
    body = json.dumps({"access_token": REVIEW}).encode()
    session = json.dumps({"vm_id": "vm1"}).encode()
    hatch = json.dumps({"token": REVIEW}).encode()
    return H.FakeHttp(
        {
            "/api/auth/check": H.HttpResponse(200, body, {}, H.PEER),
            "/api/session": H.HttpResponse(200, session, {}, H.PEER),
            "/api/hatch/token": H.HttpResponse(200, hatch, {}, H.PEER),
        }
    )


def _review_credentials() -> object:
    return H.Credentials(jar=f"hatch_sess={REVIEW}")


def test_h1_secrets_do_not_reach_persistence_repr_or_traceback_chains():
    frames: list = []

    def handler(frame):
        frames.append(frame)
        payload = {
            "ok": False,
            "error": REVIEW,
            "access_token": REVIEW,
            "token": REVIEW,
        }
        return [response_frame(frame.stream_id, 200, json.dumps(payload).encode(), end_body=True)]

    transport, _http, _guard, connector = H.build(
        handler=handler,
        http=_review_http(),
        credentials=_review_credentials(),
    )
    with transport:
        attempt = transport.chat_stream(
            H.mutation("chat.stream", {"text": "hello"}),
            authorization=H.Token(),
        )
    connector.thread.join(2)
    rendered = attempt.body.decode() + attempt.evidence.detail + repr(attempt) + repr(transport)
    assert REVIEW not in rendered
    assert "hatch_sess" not in rendered
    assert attempt.evidence.verdict == EVIDENCE_APP_ERROR
    assert json.loads(attempt.body)["ok"] is False

    def raw_handler(frame):
        body = b"\xffauth_token=" + REVIEW.encode()
        return [response_frame(frame.stream_id, 200, body, end_body=True)]

    other, _http, _guard, other_connector = H.build(
        handler=raw_handler,
        http=_review_http(),
        credentials=_review_credentials(),
    )
    with other:
        raw_attempt = other.chat_stream(
            H.mutation("chat.stream", {"text": "hello"}),
            authorization=H.Token(),
        )
    other_connector.thread.join(2)
    assert raw_attempt.body == b""
    assert raw_attempt.evidence.verdict == EVIDENCE_UNDECIDED
    assert REVIEW not in repr(raw_attempt)

    class BoomCredentials:
        def cookies(self, auth_ref: str) -> str:
            raise RuntimeError(f"auth_token={REVIEW}")

    transport, http, _guard, connector = H.build(
        handler=H.ok_frames([]),
        credentials=BoomCredentials(),
    )
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.AUTH_REQUIRED
    assert caught.value.detail.message == "Muse credentials are unavailable"
    _no_chain(caught.value)
    assert http.calls == []

    class BoomGuard(H.Guard):
        def check(self, url: str, *, purpose: str):
            raise RuntimeError(f"auth_token={REVIEW}")

    transport, http, _guard, connector = H.build(handler=H.ok_frames([]), guard=BoomGuard())
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.message == "Outbound URL check failed"
    _no_chain(caught.value)
    assert connector.urls == []

    class CoreCredentials:
        def cookies(self, auth_ref: str) -> str:
            raise CoreError(ErrorCode.AUTH_REQUIRED, f"cookie: session={REVIEW}")

    transport, _http, _guard, _connector = H.build(
        handler=H.ok_frames([]),
        credentials=CoreCredentials(),
    )
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.message == "Muse credentials are unavailable"
    assert "cookie:" not in str(caught.value)
    _no_chain(caught.value)

    class BadSocket:
        def send_bytes(self, data: bytes) -> None:
            raise RuntimeError(f"auth_token={REVIEW}")

    with pytest.raises(SendUncertain) as sent:
        CurlSocket(BadSocket(), H.PEER).send(b"frame")
    assert REVIEW not in str(sent.value)
    _no_chain(sent.value)


def test_h2_reads_stop_at_the_size_and_deadline_caps():
    from curl_cffi.const import CurlWsFlag

    class Frame:
        def __init__(self, flags: int, left: int) -> None:
            self.flags = flags
            self.bytesleft = left

    class Fragments:
        def __init__(self) -> None:
            self.calls = 0

        def recv_fragment(self):
            self.calls += 1
            return b"x" * 40000, Frame(CurlWsFlag.CONT, 1)

    fragments = Fragments()
    with pytest.raises(CoreError) as caught:
        CurlSocket(fragments, H.PEER).recv(5)
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert fragments.calls == 2

    ticks = {"n": 0}

    def monotonic() -> float:
        ticks["n"] += 1
        return 0.0 if ticks["n"] < 4 else 100.0

    class One:
        def __init__(self) -> None:
            self.calls = 0

        def recv_fragment(self):
            self.calls += 1
            return b"ping", Frame(CurlWsFlag.PING, 0)

    one = One()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(time, "monotonic", monotonic)
    try:
        assert CurlSocket(one, H.PEER).recv(5) is None
    finally:
        monkeypatch.undo()
    assert one.calls == 1

    buffer = AuthBodyBuffer()
    assert buffer(b"x" * AUTH_RESPONSE_MAX_BYTES) == AUTH_RESPONSE_MAX_BYTES
    with pytest.raises(RuntimeError, match="auth body cap"):
        buffer(b"y")
    assert buffer.overflow is True
    assert len(buffer.data) == AUTH_RESPONSE_MAX_BYTES

    padded = b'{"access_token":"abcd"}' + b" " * (AUTH_RESPONSE_MAX_BYTES + 10)
    http = H.FakeHttp({"/api/auth/check": H.HttpResponse(200, padded, {}, H.PEER)})
    transport, _http, _guard, connector = H.build(handler=H.ok_frames([]), http=http)
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.LIMIT_REACHED
    assert "abcd" not in str(caught.value)
    assert connector.urls == []

    box = [0.0]
    link = ScriptedLink()
    loop = ReceiveLoop(link, clock=lambda: box[0])
    with loop:
        stream_id = loop.open_request(verb="POST", path="/chat/stream", body=b"{}")
        frame = response_frame(stream_id, 200, b'{"ok":true}', end_body=True)

        def late(timeout: float = 0.0):
            box[0] = 31.0
            return frame

        link.recv = late  # type: ignore[method-assign]
        with pytest.raises(IncompleteBody, match="deadline"):
            loop.collect(stream_id, timeout_s=30)

    transport, http, _guard, connector = H.build(handler=H.ok_frames([]))
    with pytest.raises(MuseProgrammingError):
        transport.sessions_list(timeout_s=31)
    with pytest.raises(MuseProgrammingError):
        transport.sessions_list(timeout_s=float("nan"))
    assert http.calls == []
    assert connector.urls == []


def test_h3_post_handoff_read_errors_keep_uncertainty():
    class RaiseRead:
        def __init__(self, inner: H.MemoryEndpoint) -> None:
            self.inner = inner
            self.peer_address = inner.peer_address
            self.sent = 0

        def send(self, data: bytes) -> None:
            self.sent += 1
            self.inner.send(data)

        def recv(self, timeout: float):
            if self.sent >= 3:
                raise CoreError(ErrorCode.NETWORK_TIMEOUT, f"read broke auth_token={REVIEW}")
            return self.inner.recv(timeout)

        def close(self) -> None:
            self.inner.close()

    prepared = H.mutation("chat.stream", {"text": "hello"}, marker="req-h3")
    transport, _http, _guard, connector = H.build(
        handler=H.ok_frames([]),
        connector=H.FakeConnector(H.ok_frames([]), factory=RaiseRead),
    )
    with transport:
        attempt = transport.chat_stream(prepared, authorization=H.Token())
    connector.thread.join(2)
    assert attempt.bytes_handed_off is True
    assert attempt.state == SEND_UNCERTAIN
    assert attempt.evidence.verdict == EVIDENCE_UNDECIDED
    assert attempt.request_marker == "req-h3"
    assert attempt.stream_id == 1
    assert attempt.error_code == ErrorCode.NETWORK_TIMEOUT.value
    assert REVIEW not in repr(attempt)

    create, _http, _guard, create_connector = H.build(
        handler=H.ok_frames([]),
        connector=H.FakeConnector(H.ok_frames([]), factory=RaiseRead),
    )
    with create, pytest.raises(MuseStateUnknown) as caught:
        create.session_start(
            H.mutation("session.start", {"title": "topic"}, marker="open-h3"),
            authorization=H.Token(),
        )
    create_connector.thread.join(2)
    assert caught.value.detail.code == ErrorCode.EXTERNAL_STATE_UNKNOWN
    assert caught.value.detail.retry_class == "reconcile"
    assert caught.value.request_marker == "open-h3"
    assert caught.value.bytes_handed_off is True
    assert caught.value.stream_id == 1
    _no_chain(caught.value)

    frames: list = []
    transport, _http, _guard, connector = H.build(
        handler=lambda frame: frames.append(frame) or [H.encode_unknown_kind_frame(frame.stream_id)]
    )
    with transport, pytest.raises(MuseProtocolStop) as stopped:
        transport.chat_stream(
            H.mutation("chat.stream", {"text": "hello"}, marker="req-stop"),
            authorization=H.Token(),
        )
    connector.thread.join(2)
    assert stopped.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert stopped.value.detail.retry_class == "never"
    assert "Do not resend" in (stopped.value.detail.next_action or "")
    assert stopped.value.attempt is not None
    assert stopped.value.attempt.request_marker == "req-stop"
    assert stopped.value.attempt.bytes_handed_off is True
    assert len(frames) == 1
    _no_chain(stopped.value)


def test_h4_pins_must_match_the_canonical_host_and_the_peer():
    mismatched = CheckedURL(
        url="https://different.test/api/auth/check",
        hostname="different.test",
        port=443,
        addresses=("8.8.8.8",),
        purpose="request",
    )
    guard = H.Guard(result=mismatched)
    transport, http, _guard, connector = H.build(handler=H.ok_frames([]), guard=guard)
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.message == "Outbound pin does not match the requested URL"
    assert http.calls == []
    assert connector.urls == []
    _no_chain(caught.value)

    empty = H.Guard(addresses=())
    transport, http, _guard, connector = H.build(handler=H.ok_frames([]), guard=empty)
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert "pinned addresses" in caught.value.detail.message
    assert http.calls == []

    missing = H.Guard()
    missing.return_none = True
    transport, http, _guard, connector = H.build(handler=H.ok_frames([]), guard=missing)
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.SCOPE_REQUIRED
    assert http.calls == []

    literal = CheckedURL(
        url="https://8.8.8.8/x",
        hostname="8.8.8.8",
        port=443,
        addresses=("8.8.8.8",),
        purpose="request",
    )
    transport, _http, _guard, _connector = H.build(
        handler=H.ok_frames([]), guard=H.Guard(result=literal)
    )
    with pytest.raises(CoreError) as caught:
        transport._check_url("https://8.8.8.8/x", "session")
    assert caught.value.detail.message == "Outbound host must stay a hostname"

    http = H.FakeHttp(
        {
            "/api/auth/check": H.HttpResponse(
                200, json.dumps({"access_token": REVIEW, "note": REVIEW}).encode(), {}, "127.0.0.1"
            )
        }
    )
    transport, http, guard, connector = H.build(handler=H.ok_frames([]), http=http)
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.message == "Outbound peer was rejected"
    assert REVIEW not in str(caught.value)
    assert connector.urls == []
    assert http.calls[0]["url"].startswith("https://muse.ai/")
    assert guard.peers == ["127.0.0.1"]
    _no_chain(caught.value)

    class BadPeer:
        def connect(self, url, *, checked, timeout):
            client, _server = H.duplex()
            client.peer_address = "127.0.0.1"
            return client

    transport, _http, guard, connector = H.build(handler=H.ok_frames([]), connector=BadPeer())
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.message == "Outbound peer was rejected"
    assert guard.peers[-1] == "127.0.0.1"
    assert any(url.startswith("wss://hatch.metaaivm.com/") for _purpose, url in guard.seen)
    _no_chain(caught.value)

    with pytest.raises(CoreError) as caught:
        MuseTransport(proxy="http://127.0.0.1:9", network_mode="live")
    assert caught.value.detail.message == "Explicit proxies are not available"


def test_curl_adapter_arguments_do_not_use_the_environment(monkeypatch):
    _exercise_real_curl_options(monkeypatch)


def test_h5_real_curl_request_uses_the_session_constructor(monkeypatch):
    _exercise_real_curl_options(monkeypatch)
    text = _exercise_real_curl_options.last_error
    assert "unexpected keyword" not in text
    assert "curl_options" not in text


def test_m5_ca_bundle_ignores_environment_overrides(monkeypatch):
    recorded = _exercise_real_curl_options(monkeypatch)
    import certifi
    from curl_cffi.const import CurlOpt

    def last(option: CurlOpt):
        found = [value for opt, value in recorded if opt == int(option)]
        assert found, option
        return found[-1]

    bundle = certifi.where()
    assert last(CurlOpt.CAINFO) == bundle
    assert "/synthetic/untrusted-ca.pem" not in str(recorded)
    assert last(CurlOpt.PROXY) == b""
    assert last(CurlOpt.SSL_VERIFYPEER) == 1
    assert last(CurlOpt.SSL_VERIFYHOST) == 2
    rendered = " ".join(str(value) for _opt, value in recorded)
    assert "muse.ai:443:203.0.113.10" in rendered
    assert "muse.ai:443:[2001:db8::1]" in rendered
    assert "hatch.metaaivm.com:443:203.0.113.10" in rendered


def _exercise_real_curl_options(monkeypatch):
    from curl_cffi.const import CurlOpt
    from curl_cffi.curl import Curl

    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/synthetic/untrusted-ca.pem")
    monkeypatch.setenv("CURL_CA_BUNDLE", "/synthetic/untrusted-ca.pem")
    monkeypatch.setenv("SSL_CERT_FILE", "/synthetic/untrusted-ca.pem")
    monkeypatch.setenv("https_proxy", "http://env-proxy.invalid:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://env-proxy.invalid:8080")
    recorded: list[tuple[int, object]] = []
    real_setopt = Curl.setopt

    def setopt(self, option, value):
        recorded.append((int(option), value))
        return real_setopt(self, option, value)

    def perform(self, *args, **kwargs):
        raise RuntimeError("perform forbidden")

    def dns(*args, **kwargs):
        raise AssertionError("dns forbidden")

    monkeypatch.setattr(Curl, "setopt", setopt)
    monkeypatch.setattr(Curl, "perform", perform)
    monkeypatch.setattr(socket, "getaddrinfo", dns)
    checked = CheckedURL(
        url=MUSE_ORIGIN + "/api/auth/check",
        hostname="muse.ai",
        port=443,
        addresses=("203.0.113.10", "2001:db8::1"),
        purpose="request",
    )
    from metamuse._muse_http import CurlConnector, CurlHttp

    with pytest.raises(CoreError) as caught:
        CurlHttp().request(
            "POST",
            checked.url,
            headers={"Cookie": "hatch_sess=redacted"},
            body=b"",
            checked=checked,
            timeout=1,
        )
    assert caught.value.detail.code in {ErrorCode.NETWORK_TIMEOUT, ErrorCode.DEPENDENCY_MISSING}
    _exercise_real_curl_options.last_error = str(caught.value)
    assert "unexpected keyword" not in _exercise_real_curl_options.last_error
    assert "perform forbidden" not in _exercise_real_curl_options.last_error
    _no_chain(caught.value)
    wss = CheckedURL(
        url=HATCH_WSS_BASE + "?vm_id=vm1&auth_token=redacted",
        hostname="hatch.metaaivm.com",
        port=443,
        addresses=("203.0.113.10",),
        purpose="request",
    )
    with pytest.raises(CoreError) as caught:
        CurlConnector().connect(wss.url, checked=wss, timeout=1)
    assert "unexpected keyword" not in str(caught.value)
    assert any(opt == int(CurlOpt.CAINFO) for opt, _value in recorded)
    return recorded


def test_m1_mutations_require_a_prepared_request_and_one_consume():
    prepared = prepare_muse_request("chat.stream", marker="req-1", body={"text": "hello"})
    caller = {"text": "hello"}
    frozen = prepare_muse_request("chat.stream", marker="req-1", body=caller)
    caller["text"] = "changed"
    assert b"changed" not in frozen.body
    assert b"hello" in frozen.body

    token = H.Token()
    frames: list = []
    transport, _http, _guard, connector = H.build(handler=H.ok_frames(frames))
    offline = MuseTransport(
        auth_ref="auth-ref",
        credentials=H.Credentials(),
        guard=H.Guard(),
        network_mode="offline",
        http=H.FakeHttp(),
        connector=H.FakeConnector(H.ok_frames([])),
    )
    with pytest.raises(CoreError) as caught:
        offline.chat_stream(prepared, authorization=token)
    assert caught.value.detail.code == ErrorCode.SCOPE_DENIED
    assert token.calls == 0

    with transport:
        attempt = transport.chat_stream(prepared, authorization=token)
    connector.thread.join(2)
    assert token.calls == 1
    assert token.digests == [
        action_digest({"origin": MUSE_ORIGIN, "route": "chat.stream"}, {"text": "hello"})
    ]
    marker = next(
        header.value for header in frames[0].request.headers if header.key == "x-request-id"
    )
    assert marker == "req-1"
    assert attempt.request_marker == "req-1"
    with pytest.raises(CoreError) as caught:
        transport.chat_stream(prepared, authorization=token)
    assert caught.value.detail.message == "Submit authorization was refused"
    assert connector.urls and len(connector.threads) == 1

    for bad in (None, True, "false", object()):
        fresh, http, _guard, fresh_connector = H.build(handler=H.ok_frames([]))
        with pytest.raises(CoreError) as caught:
            fresh.chat_stream(prepared, authorization=bad)
        assert caught.value.detail.code == ErrorCode.SCOPE_REQUIRED
        assert http.calls == []
        assert fresh_connector.urls == []

    with pytest.raises(CoreError) as caught:
        prepare_muse_request(
            "chat.stream",
            marker="req-1",
            body={"text": "x" * (QUESTION_MAX_UTF8_BYTES + 1)},
        )
    assert caught.value.detail.code == ErrorCode.LIMIT_REACHED
    prepare_muse_request(
        "chat.stream", marker="req-1", body={"text": "x" * QUESTION_MAX_UTF8_BYTES}
    )
    with pytest.raises(CoreError) as caught:
        prepare_muse_request(
            "session.start",
            marker="req-1",
            body={"packet_text": "p" * (PACKET_MAX_UTF8_BYTES + 1)},
        )
    assert caught.value.detail.code == ErrorCode.LIMIT_REACHED
    prepare_muse_request(
        "session.start",
        marker="req-1",
        body={"packet_text": "p" * PACKET_MAX_UTF8_BYTES},
    )


def test_m2_malformed_wire_json_and_noise_are_protocol_errors():
    raw = bytes.fromhex("08011000180122100a0e08011a0a08c80112030a01ff2001")
    with pytest.raises(ProtocolMismatch):
        decode_server_frame(raw)
    nested = b"[" * 10000 + b"0" + b"]" * 10000
    evidence = attempt_evidence(stream_complete=True, stream_status=200, body=nested)
    assert evidence.verdict == EVIDENCE_UNDECIDED
    from metamuse.muse import _retry_after

    assert _retry_after({"Retry-After": "9" * 500}) == 0
    assert _retry_after({"Retry-After": "9" * 5000}) == 0
    exchange = Exchange(
        session_id="sess-a",
        baseline_seq=0,
        sent_text="hello",
        request_marker="req-1",
    )
    event = {"session_id": "sess-a", "event_name": "message.user", "seq": "invalid", "payload": {}}
    assert user_echo(exchange, [event]) is None

    class Damaged:
        def connect(self, url, *, checked, timeout):
            client, server = H.duplex()

            def run() -> None:
                party = NoiseParty("responder")
                first = server.recv(5)
                if not first:
                    return
                party.read(first)
                message = bytearray(party.write(b""))
                message[-1] ^= 0xFF
                server.send(bytes(message))

            threading.Thread(target=run, daemon=True).start()
            return client

    transport, _http, _guard, _connector = H.build(handler=lambda frame: [], connector=Damaged())
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert caught.value.detail.retry_class == "never"
    assert caught.value.detail.message == "Muse handshake failed"
    _no_chain(caught.value)
    with pytest.raises(ProtocolMismatch):
        NoiseParty("initiator").encrypt(b"x")


def test_m3_protocol_and_failed_writes_close_the_link():
    seen = {"n": 0}

    def handler(frame):
        seen["n"] += 1
        if seen["n"] == 1:
            from metamuse._muse_codec import encode_unknown_kind_frame

            return [encode_unknown_kind_frame(frame.stream_id)]
        return [response_frame(frame.stream_id, 200, b'{"ok":true}', end_body=True)]

    transport, _http, _guard, connector = H.build(handler=handler)
    spent = H.Token()
    prepared = H.mutation("chat.stream", {"text": "hello"}, marker="req-m3")
    with transport, pytest.raises(MuseProtocolStop):
        transport.chat_stream(prepared, authorization=spent)
    assert transport._loop is None
    with pytest.raises(CoreError) as caught:
        transport.chat_stream(prepared, authorization=spent)
    assert caught.value.detail.message == "Submit authorization was refused"
    assert len(connector.urls) == 1
    fresh = H.Token()
    attempt = transport.chat_stream(prepared, authorization=fresh)
    connector.thread.join(2)
    assert attempt.state == SENT
    assert len(connector.urls) == 2
    assert fresh.calls == 1

    def factory(inner):
        factory.calls += 1
        if factory.calls == 1:
            return H.FailAfter(inner, 2)
        return inner

    factory.calls = 0
    transport, _http, _guard, connector = H.build(
        handler=H.ok_frames([]),
        connector=H.FakeConnector(H.ok_frames([]), factory=factory),
    )
    with transport:
        failed = transport.chat_stream(
            H.mutation("chat.stream", {"text": "hello"}),
            authorization=H.Token(),
        )
    assert failed.state == FAILED_BEFORE_SEND
    assert failed.bytes_handed_off is False
    assert transport._loop is None
    with transport:
        again = transport.chat_stream(
            H.mutation("chat.stream", {"text": "hello"}),
            authorization=H.Token(),
        )
    connector.thread.join(2)
    assert again.state == SENT
    assert len(connector.urls) == 2


def test_m4_acknowledgement_is_classified_before_redaction():
    bearer = b'{"ok":true,"note":"Bearer synthetic-review-secret-42"}'
    cookie = b'{"ok":true,"note":"cookie: session=synthetic-review-secret-42"}'
    for body in (bearer, cookie):
        evidence = attempt_evidence(stream_complete=True, stream_status=200, body=body)
        assert evidence.verdict == EVIDENCE_ACKED

    def handler(frame):
        return [response_frame(frame.stream_id, 200, bearer, end_body=True)]

    transport, _http, _guard, connector = H.build(
        handler=handler,
        http=_review_http(),
        credentials=_review_credentials(),
    )
    with transport:
        attempt = transport.chat_stream(
            H.mutation("chat.stream", {"text": "hello"}),
            authorization=H.Token(),
        )
    connector.thread.join(2)
    assert attempt.evidence.verdict == EVIDENCE_ACKED
    assert attempt.state == SENT
    parsed = json.loads(attempt.body)
    assert parsed["ok"] is True
    assert REVIEW not in attempt.body.decode()


def test_m6_duplicate_responses_and_early_chunks_are_protocol_errors():
    link = ScriptedLink()
    loop = ReceiveLoop(link)
    with loop:
        stream_id = loop.open_request(verb="POST", path="/chat/stream", body=b"{}")
        link.push(response_frame(stream_id, 500, b"", end_body=False))
        link.push(response_frame(stream_id, 200, b'{"ok":true}', end_body=True))
        with pytest.raises(ProtocolMismatch, match="duplicate"):
            loop.collect(stream_id)
    assert loop.failed is True

    link = ScriptedLink()
    loop = ReceiveLoop(link)
    with loop:
        stream_id = loop.open_request(verb="POST", path="/chat/stream", body=b"{}")
        link.push(chunk_frame(stream_id, b'{"ok":true}', end_body=True))
        with pytest.raises(ProtocolMismatch, match="before application response"):
            loop.collect(stream_id)

    def handler(frame):
        return [
            response_frame(frame.stream_id, 500, b"", end_body=False),
            response_frame(frame.stream_id, 200, b'{"ok":true}', end_body=True),
        ]

    transport, _http, _guard, connector = H.build(handler=handler)
    with transport, pytest.raises(MuseProtocolStop) as caught:
        transport.chat_stream(
            H.mutation("chat.stream", {"text": "hello"}, marker="req-m6"),
            authorization=H.Token(),
        )
    connector.thread.join(2)
    assert caught.value.bytes_handed_off is True
    assert caught.value.attempt is not None
    assert caught.value.attempt.evidence.verdict != EVIDENCE_ACKED
    assert transport._loop is None
    assert MAX_COLLECT_FRAMES == 64


def test_m8_native_wheel_inventory_matches_curl_version():
    from curl_cffi import Curl

    printed = Curl().version()
    if isinstance(printed, bytes):
        printed = printed.decode()
    notice = Path(__file__).resolve().parents[1].joinpath("THIRD_PARTY_NOTICES.md").read_text()
    for token in printed.split():
        name, _slash, version = token.partition("/")
        assert name in notice
        if version:
            assert version in notice
    assert "libidn2" in notice
    assert "ngtcp2" in notice
    assert "LGPLv3" in notice or "LGPL" in notice
    assert "Unicode" in notice
    assert "libunistring/" not in notice
    assert "BoringSSL" in notice


def test_l1_varints_and_stream_ids_stay_inside_declared_ranges():
    with pytest.raises(ProtocolMismatch):
        decode_service_frame(b"\x08" + b"\xff" * 9 + b"\x7f")
    link = ScriptedLink()
    loop = ReceiveLoop(link)
    loop.next_stream_id = 2**63
    with loop, pytest.raises(ProtocolMismatch, match="exhausted"):
        loop.open_request(verb="POST", path="/chat/stream", body=b"{}")
    assert link.sent == []
    assert loop.next_stream_id == 2**63

    link = ScriptedLink()
    loop = ReceiveLoop(link)
    loop.next_stream_id = 2**63 - 1
    with loop:
        stream_id = loop.open_request(verb="GET", path="/chat/history")
    from metamuse._muse_codec import decode_client_frame

    _service, frame = decode_client_frame(link.sent[0])
    assert stream_id == 2**63 - 1
    assert frame.stream_id == 2**63 - 1
    with loop, pytest.raises(ProtocolMismatch, match="exhausted"):
        loop.open_request(verb="GET", path="/chat/history")
    assert loop.next_stream_id == 2**63

    wide = encode_server_frame(
        __import__("metamuse._muse_codec", fromlist=["ServiceFrame"]).ServiceFrame(
            stream_id=1,
            kind="response",
            response=__import__(
                "metamuse._muse_codec", fromlist=["ApplicationResponse"]
            ).ApplicationResponse(status=2**31, body=b"{}", end_body=True),
        )
    )
    with pytest.raises(ProtocolMismatch, match="int32"):
        decode_server_frame(wide)
    negative = encode_server_frame(
        __import__("metamuse._muse_codec", fromlist=["ServiceFrame"]).ServiceFrame(
            stream_id=1,
            kind="response",
            response=__import__(
                "metamuse._muse_codec", fromlist=["ApplicationResponse"]
            ).ApplicationResponse(status=-1, body=b"{}", end_body=True),
        )
    )
    decoded = decode_server_frame(negative)
    assert decoded.response is not None
    assert decoded.response.status == -1
