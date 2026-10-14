"""Offline Muse transport tests. Sockets are blocked. LIVE is not claimed."""

from __future__ import annotations

import hashlib
import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest

from metamuse._muse_codec import (
    NOISE_MAX_CIPHERTEXT_BYTES,
    ApplicationRequest,
    NoiseTransportFrame,
    ServiceFrame,
    decode_server_frame,
    encode_client_frame,
    encode_noise_transport_frame,
    encode_unknown_kind_frame,
)
from metamuse._muse_http import AddressPin, CurlConnector, CurlHttp, CurlSocket, HttpResponse
from metamuse._muse_link import (
    DEFAULT_COLLECT_TIMEOUT_S,
    MAX_COLLECT_FRAMES,
    IncompleteBody,
    ProtocolMismatch,
    ReceiveLoop,
    ScriptedLink,
    chunk_frame,
    response_frame,
)
from metamuse._muse_noise import finished_pair
from metamuse.models import CoreError, ErrorCode, canonical_json
from metamuse.muse import (
    EVIDENCE_ACKED,
    EVIDENCE_APP_ERROR,
    EVIDENCE_UNDECIDED,
    FAILED_BEFORE_SEND,
    HATCH_WSS_BASE,
    IMPLEMENTED_TOOLS,
    MUSE_ORIGIN,
    SEND_UNCERTAIN,
    SENT,
    VM_ADDRESS_TEMPLATE,
    Exchange,
    MuseProgrammingError,
    MuseTransport,
    close_muse_exchange,
    open_muse_discussion,
    read_muse_exchange,
    register_operations,
    submit_muse_exchange,
)

SECRET = "supersecret"
COOKIE = f"hatch_sess={SECRET}"
ROOT = Path(__file__).resolve().parents[1]


class MemoryEndpoint:
    def __init__(self) -> None:
        self.peer: MemoryEndpoint | None = None
        self.queue: queue.Queue[bytes | None] = queue.Queue()

    def send(self, data: bytes) -> None:
        if self.peer is None:
            raise RuntimeError("pipe has no peer")
        self.peer.queue.put(data)

    def recv(self, timeout: float = 0.0) -> bytes | None:
        try:
            if timeout <= 0:
                return self.queue.get_nowait()
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self.queue.put(None)
        if self.peer is not None:
            self.peer.queue.put(None)


def duplex() -> tuple[MemoryEndpoint, MemoryEndpoint]:
    left, right = MemoryEndpoint(), MemoryEndpoint()
    left.peer = right
    right.peer = left
    return left, right


class RawWire:
    def __init__(self, data: bytes) -> None:
        self.data = data


def serve(link: MemoryEndpoint, handler, errors: list[BaseException]) -> None:
    from metamuse._muse_codec import decode_client_frame
    from metamuse._muse_noise import NoiseParty

    try:
        party = NoiseParty("responder")
        first = link.recv(5)
        if not first:
            return
        if party.read(first) != b"":
            return
        link.send(party.write(b""))
        third = link.recv(5)
        if not third:
            return
        if party.read(third) != b"":
            return
        while True:
            raw = link.recv(5)
            if not raw:
                return
            _service, frame = decode_client_frame(party.decrypt(raw))
            result = handler(frame)
            if isinstance(result, RawWire):
                link.send(result.data)
                continue
            for piece in result:
                link.send(party.encrypt(piece))
    except Exception as exc:  # recorded for the test, not logged
        errors.append(exc)


class Credentials:
    def __init__(self, jar: str | None = COOKIE) -> None:
        self.jar = jar
        self.refs: list[str] = []

    def cookies(self, auth_ref: str) -> str:
        self.refs.append(auth_ref)
        if self.jar is None:
            raise CoreError(ErrorCode.AUTH_REQUIRED, "no jar")
        if not isinstance(self.jar, str):
            raise CoreError(ErrorCode.AUTH_REQUIRED, "bad jar")
        return self.jar


class Guard:
    def __init__(self, pin: AddressPin | None = None, log: list[str] | None = None) -> None:
        self.pin = pin
        self.log = log
        self.seen: list[tuple[str, str]] = []

    def allow(self, url: str, *, purpose: str) -> AddressPin | None:
        self.seen.append((purpose, url))
        if self.log is not None:
            self.log.append("guard:" + purpose)
        return self.pin


class FakeHttp:
    def __init__(
        self,
        responses: dict | None = None,
        log: list[str] | None = None,
    ) -> None:
        self.responses = responses or {}
        self.calls: list[dict] = []
        self.log = log

    def request(self, method, url, *, headers, body, pins, timeout, proxy):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "body": body,
                "pins": pins,
                "timeout": timeout,
                "proxy": proxy,
            }
        )
        if self.log is not None:
            self.log.append("http:" + urlparse(url).path)
        path = urlparse(url).path
        if path in self.responses:
            return self.responses[path]
        if path == "/api/auth/check":
            return HttpResponse(200, json.dumps({"access_token": SECRET}).encode(), {})
        if path == "/api/session":
            return HttpResponse(200, json.dumps({"vm_id": "vm1"}).encode(), {})
        if path == "/api/hatch/token":
            return HttpResponse(200, json.dumps({"token": SECRET}).encode(), {})
        raise AssertionError(path)


class FakeConnector:
    def __init__(self, handler, log: list[str] | None = None, factory=None) -> None:
        self.handler = handler
        self.log = log
        self.factory = factory
        self.urls: list[str] = []
        self.pins: list[AddressPin | None] = []
        self.proxies: list[str | None] = []
        self.errors: list[BaseException] = []
        self.thread: threading.Thread | None = None

    def connect(self, url, *, pins, timeout, proxy):
        self.urls.append(url)
        self.pins.append(pins)
        self.proxies.append(proxy)
        if self.log is not None:
            self.log.append("ws")
        client, server = duplex()
        if self.factory is not None:
            client = self.factory(client)
        self.thread = threading.Thread(
            target=serve, args=(server, self.handler, self.errors), daemon=True
        )
        self.thread.start()
        return client


class FailAfter:
    def __init__(self, inner: MemoryEndpoint, succeed: int) -> None:
        self.inner = inner
        self.left = succeed

    def send(self, data: bytes) -> None:
        if self.left <= 0:
            raise RuntimeError(f"blocked before write auth_token={SECRET}")
        self.left -= 1
        self.inner.send(data)

    def recv(self, timeout: float) -> bytes | None:
        return self.inner.recv(timeout)

    def close(self) -> None:
        self.inner.close()


def ok_frames(frames: list) -> object:
    def handler(frame):
        frames.append(frame)
        path = frame.request.path
        if path.startswith("/session/start"):
            body = b'{"ok":true,"session_id":"sess-a"}'
        else:
            body = b'{"ok":true}'
        return [response_frame(frame.stream_id, 200, body, end_body=True)]

    return handler


def build(*, handler, http=None, guard=None, connector=None, **kwargs):
    log = kwargs.pop("log", None)
    http = http if http is not None else FakeHttp(log=log)
    guard = guard if guard is not None else Guard(log=log)
    connector = connector if connector is not None else FakeConnector(handler, log=log)
    transport = MuseTransport(
        auth_ref=kwargs.pop("auth_ref", "auth-ref"),
        credentials=kwargs.pop("credentials", Credentials()),
        guard=guard,
        network_mode=kwargs.pop("network_mode", "live"),
        http=http,
        connector=connector,
        clock=kwargs.pop("clock", None),
        proxy=kwargs.pop("proxy", None),
    )
    return transport, http, guard, connector


def assert_clean(text: str) -> None:
    assert SECRET not in text
    assert "hatch_sess" not in text


def test_lifecycle_stays_unimplemented():
    assert IMPLEMENTED_TOOLS == frozenset()
    register_operations(None)
    for function in (
        open_muse_discussion,
        submit_muse_exchange,
        read_muse_exchange,
        close_muse_exchange,
    ):
        with pytest.raises(CoreError) as caught:
            function(None, None)  # type: ignore[arg-type]
        assert caught.value.detail.code == ErrorCode.UNSUPPORTED


def test_import_does_not_load_network_libraries():
    code = (
        "import sys, metamuse.muse; "
        "assert 'curl_cffi' not in sys.modules; "
        "assert 'noise' not in sys.modules"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_defaults_refuse_before_any_io():
    connector = FakeConnector(ok_frames([]))
    http = FakeHttp()
    offline = MuseTransport(http=http, connector=connector)
    with pytest.raises(CoreError) as caught:
        offline.sessions_list()
    assert caught.value.detail.code == ErrorCode.SCOPE_DENIED
    assert caught.value.detail.retry_class == "user_action"
    assert http.calls == []
    assert connector.urls == []

    live = MuseTransport(network_mode="live", http=http, connector=connector)
    with pytest.raises(CoreError) as caught:
        live.sessions_list()
    assert caught.value.detail.code == ErrorCode.AUTH_REQUIRED

    guarded = MuseTransport(
        network_mode="live",
        auth_ref="auth-ref",
        credentials=Credentials(),
        http=http,
        connector=connector,
    )
    with pytest.raises(CoreError) as caught:
        guarded.chat_stream({"text": "hello"}, external_submit=True)
    assert caught.value.detail.code == ErrorCode.SCOPE_REQUIRED
    assert connector.urls == []


def test_route_outside_the_allowlist_is_a_programming_error():
    log: list[str] = []
    transport, http, _guard, connector = build(handler=ok_frames([]), log=log)
    for route in ("cmd_raw", "chat.cancel", "chat.subscribe"):
        with pytest.raises(MuseProgrammingError):
            transport.call(route)
    assert http.calls == []
    assert connector.urls == []
    assert log == []
    offline = MuseTransport()
    with pytest.raises(MuseProgrammingError):
        offline.call("session.delete")


def test_offline_and_missing_submit_do_not_connect():
    transport, http, _guard, connector = build(handler=ok_frames([]), network_mode="offline")
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.SCOPE_DENIED
    assert "offline" in caught.value.detail.message
    transport._network_mode = "live"
    with pytest.raises(CoreError) as caught:
        transport.chat_stream({"text": "hello"})
    assert caught.value.detail.code == ErrorCode.SCOPE_REQUIRED
    assert "before_external_action" in (caught.value.detail.next_action or "")
    assert http.calls == []
    assert connector.urls == []


def test_handshake_and_serial_calls_use_monotonic_stream_ids():
    frames: list = []
    log: list[str] = []
    pin = AddressPin(host="muse.ai", port=443, addresses=("203.0.113.10",))
    transport, http, guard, connector = build(
        handler=ok_frames(frames),
        log=log,
        guard=Guard(pin=pin, log=log),
    )
    with transport:
        started = transport.session_start({"title": "topic"}, external_submit=True)
        sent = transport.chat_stream({"text": "hello muse"}, external_submit=True)
        again = transport.sessions_list()
    assert connector.thread is not None
    connector.thread.join(2)
    assert connector.errors == []
    assert started.stream_id == 1
    assert sent.stream_id == 2
    assert again.stream_id == 3
    assert sent.state == SENT
    assert sent.evidence.verdict == EVIDENCE_ACKED
    assert sent.bytes_handed_off is True
    assert [frame.stream_id for frame in frames] == [1, 2, 3]
    assert frames[0].request.verb == "POST"
    assert frames[0].request.path == "/session/start"
    assert frames[2].request.verb == "GET"
    assert frames[2].request.path == "/api/session/list"
    assert all(header.key != "content-type" for header in frames[2].request.headers)
    marker = next(
        header.value for header in frames[1].request.headers if header.key == "x-request-id"
    )
    assert marker == sent.request_marker
    assert any(header.key == "content-type" for header in frames[1].request.headers)
    digest = hashlib.sha256(canonical_json({"title": "topic"})).hexdigest()
    assert started.outbound_digest == digest
    assert [purpose for purpose, _url in guard.seen] == [
        "auth.check",
        "session",
        "hatch.token",
        "wss.noise",
    ]
    assert log == [
        "guard:auth.check",
        "http:/api/auth/check",
        "guard:session",
        "http:/api/session",
        "guard:hatch.token",
        "http:/api/hatch/token",
        "guard:wss.noise",
        "ws",
    ]
    assert http.calls[0]["headers"]["Cookie"] == COOKIE
    assert "Authorization" not in http.calls[0]["headers"]
    assert http.calls[2]["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert http.calls[0]["pins"] == pin
    assert connector.pins == [pin]
    assert connector.proxies == [None]
    hatch = json.loads(http.calls[2]["body"])
    assert hatch["vmAddress"] == VM_ADDRESS_TEMPLATE.format(vm_id="vm1")
    assert connector.urls[0].startswith(HATCH_WSS_BASE + "?vm_id=vm1&auth_token=")
    assert "vm1.metaaivm.com" not in connector.urls[0]
    assert len(http.calls) == 3
    blob = repr(transport) + repr(started) + repr(sent) + repr(again)
    assert_clean(blob)
    assert SECRET not in started.body.decode()
    assert transport._credentials.refs == ["auth-ref"]


def test_history_omits_transcript_mode():
    frames: list = []
    transport, _http, _guard, connector = build(handler=ok_frames(frames))
    with transport:
        transport.chat_history(session_id="sess-a", limit=10)
        transport.call(
            "chat.history",
            query={"session_id": "sess-b", "limit": 2, "transcript_mode": "full"},
        )
    connector.thread.join(2)
    assert connector.errors == []
    assert frames[0].request.path == "/chat/history?session_id=sess-a&limit=10"
    assert "transcript_mode" not in frames[1].request.path
    assert "session_id=sess-b" in frames[1].request.path


def test_session_start_timeout_is_unknown_and_does_not_create_another():
    frames: list = []

    def handler(frame):
        frames.append(frame)
        if frame.request.path.startswith("/session/start"):
            return []
        return [response_frame(frame.stream_id, 200, b'{"ok":true}', end_body=True)]

    transport, _http, _guard, connector = build(handler=handler)
    with transport:
        with pytest.raises(CoreError) as caught:
            transport.session_start({"title": "topic"}, external_submit=True, timeout_s=0)
        listed = transport.sessions_list()
    connector.thread.join(2)
    assert connector.errors == []
    assert caught.value.detail.code == ErrorCode.EXTERNAL_STATE_UNKNOWN
    assert caught.value.detail.retry_class == "reconcile"
    assert "sessions.list" in (caught.value.detail.next_action or "")
    assert_clean(str(caught.value))
    assert [frame.request.path for frame in frames] == ["/session/start", "/api/session/list"]
    assert listed.route == "sessions.list"
    assert listed.bytes_handed_off is True


def handler_for(frames: list):
    def handler(frame):
        frames.append(frame)
        return []

    return handler


def test_chat_timeout_stays_uncertain_and_echo_promotes_without_a_second_send():
    frames: list = []
    handler = handler_for(frames)

    transport, _http, _guard, connector = build(handler=handler)
    with transport:
        silent = transport.chat_stream({"text": "hello muse"}, external_submit=True, timeout_s=0)
    connector.thread.join(2)
    assert connector.errors == []
    assert silent.bytes_handed_off is True
    assert silent.state == SEND_UNCERTAIN
    assert silent.evidence.verdict == EVIDENCE_UNDECIDED
    assert silent.error_code == ErrorCode.NETWORK_TIMEOUT.value
    assert len(frames) == 1
    assert_clean(repr(silent))

    promoted_frames: list = []
    other, _http, _guard, other_connector = build(handler=handler_for(promoted_frames))
    exchange = Exchange(
        session_id="sess-a",
        baseline_seq=10,
        sent_text="hello muse",
        request_marker="req-1",
    )
    echo = {
        "session_id": "sess-a",
        "event_name": "message.user",
        "seq": 11,
        "payload": {"display_text": "hello muse", "role": "user"},
    }
    with other:
        promoted = other.chat_stream(
            {"text": "hello muse"},
            external_submit=True,
            timeout_s=0,
            exchange=exchange,
            events=[echo],
        )
    other_connector.thread.join(2)
    assert promoted.state == SENT
    assert promoted.evidence.verdict == EVIDENCE_UNDECIDED
    assert promoted.request_marker == "req-1"
    assert promoted.bytes_handed_off is True
    assert len(promoted_frames) == 1
    assert_clean(repr(promoted))


def test_app_error_stays_uncertain_until_this_echo():
    frames: list = []

    def handler(frame):
        frames.append(frame)
        body = b'{"ok":false,"error":"auth_token=supersecret"}'
        return [response_frame(frame.stream_id, 200, body, end_body=True)]

    transport, _http, _guard, connector = build(handler=handler)
    with transport:
        attempt = transport.chat_stream({"text": "hello muse"}, external_submit=True)
    connector.thread.join(2)
    assert connector.errors == []
    assert attempt.bytes_handed_off is True
    assert attempt.evidence.verdict == EVIDENCE_APP_ERROR
    assert attempt.state == SEND_UNCERTAIN
    assert len(frames) == 1
    assert_clean(attempt.evidence.detail + attempt.body.decode() + repr(attempt))
    assert "auth_token=[redacted]" in attempt.evidence.detail

    promoted_frames: list = []

    def promoted_handler(frame):
        promoted_frames.append(frame)
        body = b'{"ok":false,"error":"rejected"}'
        return [response_frame(frame.stream_id, 200, body, end_body=True)]

    other, _http, _guard, other_connector = build(handler=promoted_handler)
    exchange = Exchange(
        session_id="sess-a",
        baseline_seq=10,
        sent_text="hello muse",
        request_marker="req-1",
    )
    echo = {
        "session_id": "sess-a",
        "event_name": "message.user",
        "seq": 11,
        "message_id": "u-11",
        "payload": {"display_text": "hello muse", "role": "user"},
    }
    with other:
        promoted = other.chat_stream(
            {"text": "hello muse"},
            external_submit=True,
            exchange=exchange,
            events=[echo],
        )
    other_connector.thread.join(2)
    assert promoted.state == SENT
    assert promoted.evidence.verdict == EVIDENCE_APP_ERROR
    assert len(promoted_frames) == 1


def test_never_sent_is_failed_before_send():
    frames: list = []
    transport, _http, _guard, connector = build(
        handler=ok_frames(frames),
        connector=FakeConnector(ok_frames(frames), factory=lambda inner: FailAfter(inner, 2)),
    )
    with transport:
        attempt = transport.chat_stream({"text": "hello muse"}, external_submit=True)
    connector.thread.join(2)
    assert attempt.bytes_handed_off is False
    assert attempt.state == FAILED_BEFORE_SEND
    assert attempt.stream_id is None
    assert attempt.evidence.verdict == EVIDENCE_UNDECIDED
    assert frames == []
    assert_clean(repr(attempt) + attempt.evidence.detail)


def test_protocol_mismatch_after_handoff_does_not_resend():
    cases = {
        "unknown": lambda frame: [encode_unknown_kind_frame(frame.stream_id)],
        "multi": lambda frame: [bytes([0x08, 0x01, 0x10, 0x00, 0x18, 0x02, 0x22, 0x04]) + b"half"],
        "oversize": lambda frame: RawWire(b"x" * (NOISE_MAX_CIPHERTEXT_BYTES + 1)),
    }
    for name, handler in cases.items():
        frames: list = []

        def wrapped(frame, handler=handler, seen=frames):
            seen.append(frame)
            return handler(frame)

        transport, _http, _guard, connector = build(handler=wrapped)
        with transport, pytest.raises(CoreError) as caught:
            transport.chat_stream({"text": "hello"}, external_submit=True)
        connector.thread.join(2)
        assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH, name
        assert caught.value.detail.retry_class == "never"
        assert "Do not resend" in (caught.value.detail.next_action or "")
        assert len(frames) == 1, name
        assert_clean(str(caught.value))


def test_nonempty_handshake_payload_is_protocol_mismatch():
    class Attested:
        urls: list[str] = []

        def connect(self, url, *, pins, timeout, proxy):
            self.urls.append(url)
            client, server = duplex()

            def run() -> None:
                from metamuse._muse_noise import NoiseParty

                party = NoiseParty("responder")
                first = server.recv(5)
                party.read(first or b"")
                server.send(bytes(party.conn.write_message(b"attest")))

            threading.Thread(target=run, daemon=True).start()
            return client

    connector = Attested()
    transport, _http, _guard, _connector = build(handler=lambda frame: [], connector=connector)
    with transport, pytest.raises(ProtocolMismatch, match="attested") as caught:
        transport.sessions_list()
    assert_clean(str(caught.value))


def test_second_receive_loop_and_concurrent_call_are_rejected():
    frames: list = []
    transport, http, _guard, connector = build(handler=ok_frames(frames))
    with transport:
        transport.sessions_list()
        intruder = ReceiveLoop(transport._loop.link, noise=transport._loop.noise)
        with pytest.raises(ProtocolMismatch, match="second receive consumer"), intruder:
            pass
        status_ok = transport.chat_history(session_id="sess-a")
    assert status_ok.status == 200
    connector.thread.join(2)
    assert connector.errors == []

    started = threading.Event()
    release = threading.Event()

    class BlockingHttp(FakeHttp):
        def request(self, method, url, *, headers, body, pins, timeout, proxy):
            if not started.is_set():
                started.set()
                assert release.wait(3)
            return super().request(
                method, url, headers=headers, body=body, pins=pins, timeout=timeout, proxy=proxy
            )

    blocker = BlockingHttp()
    busy, _http, _guard, connector = build(
        handler=ok_frames([]),
        http=blocker,
        connector=FakeConnector(ok_frames([])),
    )
    errors: list[str] = []

    def owner() -> None:
        try:
            busy.sessions_list()
        except Exception as exc:  # surfaced below if the owner itself fails
            errors.append(repr(exc))

    def rival() -> None:
        assert started.wait(3)
        try:
            busy.chat_history()
        except ProtocolMismatch as exc:
            errors.append(str(exc))
        finally:
            release.set()

    first = threading.Thread(target=owner)
    second = threading.Thread(target=rival)
    first.start()
    second.start()
    second.join(3)
    first.join(3)
    assert not first.is_alive()
    assert errors == ["concurrent collection"]
    connector.thread.join(2)


def test_vm_unavailable_is_not_an_auth_failure():
    body = (
        b'{"status":"unavailable","vm_resolution_issue":{"kind":"retryable","token":"supersecret"}}'
    )
    http = FakeHttp(
        {
            "/api/auth/check": HttpResponse(200, json.dumps({"access_token": SECRET}).encode(), {}),
            "/api/session": HttpResponse(200, body, {}),
        }
    )
    transport, http, _guard, connector = build(handler=ok_frames([]), http=http)
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.RESOURCE_UNAVAILABLE
    assert caught.value.detail.retry_class == "user_action"
    assert "retryable" not in str(caught.value)
    assert_clean(str(caught.value))
    assert connector.urls == []


def test_auth_rate_limit_redirect_and_bad_vm_id():
    secret_body = b'{"error":"auth_token=supersecret"}'
    cases = [
        (
            HttpResponse(401, secret_body, {}),
            ErrorCode.AUTH_REQUIRED,
            "user_action",
        ),
        (
            HttpResponse(429, secret_body, {"Retry-After": "12"}),
            ErrorCode.RATE_LIMITED,
            "safe_read",
        ),
        (
            HttpResponse(302, b"", {"Location": f"https://evil.example/?auth_token={SECRET}"}),
            ErrorCode.SCOPE_REQUIRED,
            "user_action",
        ),
        (
            HttpResponse(503, secret_body, {}),
            ErrorCode.RESOURCE_UNAVAILABLE,
            "user_action",
        ),
    ]
    for response, code, retry in cases:
        http = FakeHttp({"/api/auth/check": response})
        transport, _http, _guard, connector = build(handler=ok_frames([]), http=http)
        with pytest.raises(CoreError) as caught:
            transport.sessions_list()
        assert caught.value.detail.code == code
        assert caught.value.detail.retry_class == retry
        rendered = str(caught.value) + (caught.value.detail.next_action or "")
        assert_clean(rendered)
        assert "evil.example" not in rendered
        assert connector.urls == []
        if code == ErrorCode.RATE_LIMITED:
            assert caught.value.retry_after == 12

    http = FakeHttp(
        {
            "/api/auth/check": HttpResponse(200, json.dumps({"access_token": SECRET}).encode(), {}),
            "/api/session": HttpResponse(200, b'{"vm_id":"../evil"}', {}),
        }
    )
    transport, _http, _guard, connector = build(handler=ok_frames([]), http=http)
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert connector.urls == []
    assert_clean(str(caught.value))


def test_connector_errors_are_redacted():
    class Boom:
        def connect(self, url, *, pins, timeout, proxy):
            raise RuntimeError(
                f"failed {url} Authorization: Bearer {SECRET} cookie: hatch_sess={SECRET}"
            )

    transport, _http, _guard, _connector = build(handler=ok_frames([]), connector=Boom())
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.NETWORK_TIMEOUT
    rendered = str(caught.value)
    assert_clean(rendered)
    assert "auth_token=[redacted]" in rendered
    assert "Bearer [redacted]" in rendered
    assert_clean(repr(transport))


def test_missing_curl_and_noise_are_dependency_errors(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def block_curl(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "curl_cffi" or name.startswith("curl_cffi."):
            raise ImportError("curl blocked")
        return real_import(name, globals, locals, fromlist, level)

    saved_curl = {
        key: sys.modules[key]
        for key in list(sys.modules)
        if key == "curl_cffi" or key.startswith("curl_cffi.")
    }
    for key in saved_curl:
        monkeypatch.delitem(sys.modules, key, raising=False)
    monkeypatch.setattr(builtins, "__import__", block_curl)
    with pytest.raises(CoreError) as caught:
        CurlHttp().request(
            "GET",
            MUSE_ORIGIN + "/api/session",
            headers={},
            body=None,
            pins=None,
            timeout=1,
            proxy=None,
        )
    assert caught.value.detail.code == ErrorCode.DEPENDENCY_MISSING
    monkeypatch.setattr(builtins, "__import__", real_import)
    for key, module in saved_curl.items():
        monkeypatch.setitem(sys.modules, key, module)

    def block_noise(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "noise" or name.startswith("noise."):
            raise ImportError("noise blocked")
        return real_import(name, globals, locals, fromlist, level)

    for key in list(sys.modules):
        if key == "noise" or key.startswith("noise."):
            monkeypatch.delitem(sys.modules, key, raising=False)
    monkeypatch.setattr(builtins, "__import__", block_noise)
    transport, _http, _guard, connector = build(handler=ok_frames([]))
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.DEPENDENCY_MISSING
    assert_clean(str(caught.value))
    connector.thread.join(2)


def test_curl_adapter_arguments_do_not_use_the_environment(monkeypatch):
    monkeypatch.setenv("https_proxy", "http://env-proxy.invalid:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://env-proxy.invalid:8080")
    monkeypatch.setenv("http_proxy", "http://env-proxy.invalid:8080")
    captured: dict = {}

    class FakeResponse:
        status_code = 204
        content = b""
        headers: dict[str, str] = {}

    class FakeSession:
        def __init__(self, **kwargs):
            captured["init"] = kwargs

        def request(self, method, url, **kwargs):
            captured["request"] = (method, url, kwargs)
            return FakeResponse()

        def close(self):
            captured["closed"] = True

    class FakeSocket:
        def connect(self, url, **kwargs):
            captured["connect"] = (url, kwargs)
            return self

    class FakeOpt:
        PROXY = "PROXY"
        RESOLVE = "RESOLVE"

    monkeypatch.setattr(
        "metamuse._muse_http.load_curl",
        lambda: (FakeSession, FakeSocket, FakeOpt),
    )
    pin = AddressPin("muse.ai", 443, ("203.0.113.10", "2001:db8::1"))
    CurlHttp().request(
        "POST",
        MUSE_ORIGIN + "/api/auth/check",
        headers={"Cookie": COOKIE},
        body=b"",
        pins=pin,
        timeout=20,
        proxy=None,
    )
    assert captured["init"] == {"impersonate": "chrome", "verify": True, "trust_env": False}
    _method, _url, kwargs = captured["request"]
    assert kwargs["impersonate"] == "chrome"
    assert kwargs["verify"] is True
    assert kwargs["allow_redirects"] is False
    assert "proxy" not in kwargs
    assert "proxies" not in kwargs
    assert kwargs["curl_options"][FakeOpt.PROXY] == b""
    assert kwargs["curl_options"][FakeOpt.RESOLVE] == [
        "muse.ai:443:203.0.113.10",
        "muse.ai:443:[2001:db8::1]",
    ]
    assert "env-proxy" not in repr(kwargs)
    CurlConnector().connect(
        HATCH_WSS_BASE + f"?vm_id=vm1&auth_token={SECRET}",
        pins=None,
        timeout=20,
        proxy=None,
    )
    _ws_url, ws_kwargs = captured["connect"]
    assert ws_kwargs["impersonate"] == "chrome"
    assert ws_kwargs["verify"] is True
    assert ws_kwargs["allow_redirects"] is False
    assert ws_kwargs["quote"] is False
    assert "proxy" not in ws_kwargs
    assert ws_kwargs["curl_options"][FakeOpt.PROXY] == b""
    CurlConnector().connect(HATCH_WSS_BASE, pins=None, timeout=5, proxy="http://127.0.0.1:9")
    _ws_url, ws_kwargs = captured["connect"]
    assert ws_kwargs["proxy"] == "http://127.0.0.1:9"
    assert FakeOpt.PROXY not in ws_kwargs["curl_options"]


def test_curl_recv_with_zero_timeout_does_not_block():
    from curl_cffi.const import CurlECode
    from curl_cffi.curl import CurlError

    class Socket:
        def recv_fragment(self):
            raise CurlError("again", CurlECode.AGAIN)

    started = time.monotonic()
    assert CurlSocket(Socket()).recv(0) is None
    assert time.monotonic() - started < 0.5


def test_noise_round_trip_and_ciphertext_cap():
    initiator, responder = finished_pair()
    assert initiator.finished and responder.finished
    plaintext = b"x" * 65519
    ciphertext = initiator.encrypt(plaintext)
    assert len(ciphertext) == NOISE_MAX_CIPHERTEXT_BYTES
    assert responder.decrypt(ciphertext) == plaintext
    with pytest.raises(ProtocolMismatch):
        initiator.encrypt(b"x" * 65520)
    with pytest.raises(ProtocolMismatch):
        responder.decrypt(b"y" * (NOISE_MAX_CIPHERTEXT_BYTES + 1))


def test_collect_caps_deadline_and_single_consumer():
    assert DEFAULT_COLLECT_TIMEOUT_S == 30
    assert MAX_COLLECT_FRAMES == 64
    link = ScriptedLink()
    loop = ReceiveLoop(link, clock=lambda: 5.0)
    with loop:
        stream_id = loop.open_request(verb="POST", path="/chat/stream", body=b"{}")
        link.push(response_frame(stream_id, 200, b'{"ok":true}', end_body=True))
        with pytest.raises(IncompleteBody, match="deadline") as caught:
            loop.collect(stream_id, timeout_s=0)
    assert caught.value.detail.code == ErrorCode.NETWORK_TIMEOUT

    link = ScriptedLink()
    loop = ReceiveLoop(link)
    with loop:
        stream_id = loop.open_request(verb="POST", path="/chat/stream", body=b"{}")
        for _ in range(MAX_COLLECT_FRAMES + 1):
            link.push(chunk_frame(stream_id, b"", end_body=False))
        link.push(response_frame(stream_id, 200, b'{"ok":true}', end_body=True))
        with pytest.raises(IncompleteBody, match="frame budget") as caught:
            loop.collect(stream_id)
    assert caught.value.detail.code == ErrorCode.LIMIT_REACHED

    link = ScriptedLink()
    loop = ReceiveLoop(link)
    with loop:
        stream_id = loop.open_request(verb="GET", path="/chat/history")
        link.push(encode_unknown_kind_frame(stream_id))
        with pytest.raises(ProtocolMismatch, match="unknown frame type"):
            loop.collect(stream_id)

    raw = bytes([0x08, 0x01, 0x10, 0x00, 0x18, 0x02, 0x22, 0x04]) + b"half"
    with pytest.raises(ProtocolMismatch, match="multi-chunk"):
        decode_server_frame(raw)
    with pytest.raises(ProtocolMismatch, match="multi-chunk"):
        encode_noise_transport_frame(
            NoiseTransportFrame(chunk_id=1, chunk_index=0, total_chunks=2, payload=b"half")
        )

    oversized = b"z" * (65519 + 1)
    with pytest.raises(ProtocolMismatch):
        encode_client_frame(
            0,
            ServiceFrame(
                stream_id=1,
                kind="request",
                request=ApplicationRequest("POST", "/chat/stream", [], oversized, True),
            ),
            1,
        )

    link = ScriptedLink()
    first = ReceiveLoop(link)
    second = ReceiveLoop(link)
    with first:
        with pytest.raises(ProtocolMismatch, match="second receive consumer"), second:
            pass
        stream_id = first.open_request(verb="POST", path="/chat/stream", body=b"{}")
        link.push(response_frame(stream_id, 200, b'{"ok":true}', end_body=False))
        with pytest.raises(IncompleteBody, match="incomplete"):
            first.collect(stream_id)

    inside = threading.Event()
    release = threading.Event()
    link = ScriptedLink()
    loop = ReceiveLoop(link)
    original = link.recv

    def blocking_recv(timeout: float = 0.0):
        inside.set()
        assert release.wait(2)
        return original(timeout)

    link.recv = blocking_recv  # type: ignore[method-assign]
    errors: list[str] = []

    def owner() -> None:
        with loop:
            stream_id = loop.open_request(verb="POST", path="/chat/stream", body=b"{}")
            link.push(response_frame(stream_id, 200, b'{"ok":true}', end_body=True))
            status, body = loop.collect(stream_id)
            assert status == 200
            assert body == b'{"ok":true}'

    def rival() -> None:
        assert inside.wait(2)
        try:
            loop.collect(1)
        except ProtocolMismatch as exc:
            errors.append(str(exc))
        finally:
            release.set()

    owner_thread = threading.Thread(target=owner)
    rival_thread = threading.Thread(target=rival)
    owner_thread.start()
    rival_thread.start()
    rival_thread.join(3)
    owner_thread.join(3)
    assert errors and "concurrent" in errors[0]


def test_empty_pins_are_refused():
    with pytest.raises(CoreError) as caught:
        from metamuse._muse_http import resolve_entries

        resolve_entries(AddressPin("muse.ai", 443, ()))
    assert caught.value.detail.code == ErrorCode.SCOPE_REQUIRED
