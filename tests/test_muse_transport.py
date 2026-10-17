"""Offline Muse transport tests. Sockets are blocked. LIVE is not claimed."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse, urlsplit

import pytest

from metamuse._muse_codec import (
    NOISE_MAX_CIPHERTEXT_BYTES,
    ApplicationRequest,
    NoiseTransportFrame,
    ServiceFrame,
    _bytes_field,
    _iter_fields,
    decode_server_frame,
    encode_client_frame,
    encode_noise_transport_frame,
    encode_unknown_kind_frame,
)
from metamuse._muse_http import CurlHttp, CurlSocket, HttpResponse, resolve_entries
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
from metamuse._muse_noise import NoiseParty, finished_pair
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
    PreparedMuseRequest,
    prepare_muse_request,
    register_operations,
)
from metamuse.paths import WorkspacePaths
from metamuse.safety import CheckedURL
from metamuse.store import Store

SECRET = "supersecret"
REVIEW_SECRET = "synthetic-review-secret-42"
COOKIE = f"hatch_sess={SECRET}"
PEER = "203.0.113.10"
ROOT = Path(__file__).resolve().parents[1]


def checked_url(
    url: str,
    addresses: tuple[str, ...] = (PEER,),
    *,
    hostname: str | None = None,
    port: int | None = None,
    canonical: str | None = None,
) -> CheckedURL:
    parts = urlsplit(url)
    return CheckedURL(
        url=canonical if canonical is not None else url,
        hostname=hostname if hostname is not None else (parts.hostname or ""),
        port=port if port is not None else (parts.port or 443),
        addresses=addresses,
        purpose="request",
    )


class Token:
    """One-use stand-in for the core token B10 will mint."""

    def __init__(self, *, refuse: bool = False) -> None:
        self.refuse = refuse
        self.digests: list[str] = []
        self.calls = 0

    def consume(self, *, action_digest: str) -> None:
        self.calls += 1
        if self.refuse or self.calls > 1:
            raise RuntimeError(f"authorization refused auth_token={REVIEW_SECRET}")
        self.digests.append(action_digest)


def mutation(route: str, body: dict | None, marker: str = "req-1") -> PreparedMuseRequest:
    return prepare_muse_request(route, marker=marker, body=body)


def assert_no_chain(exc: BaseException) -> None:
    assert exc.__cause__ is None
    assert exc.__context__ is None


class MemoryEndpoint:
    def __init__(self) -> None:
        self.peer: MemoryEndpoint | None = None
        self.queue: queue.Queue[bytes | None] = queue.Queue()
        self.peer_address = PEER

    def send(self, data: bytes) -> None:
        if self.peer is None:
            raise RuntimeError("pipe has no peer")
        self.peer.queue.put(data)

    def recv(self, timeout: float | None = 0.0) -> bytes | None:
        try:
            if timeout is None:
                return self.queue.get()
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
        first = link.recv(None)
        if not first:
            return
        challenge = dict((number, value) for number, wire, value in _iter_fields(party.read(first)))
        nonce = challenge[1]
        key = party.conn.noise_protocol.handshake_state.s.public_bytes
        bundle = _bytes_field(2, _bytes_field(1, key) + _bytes_field(2, nonce))
        link.send(party.write(bundle))
        third = link.recv(None)
        if not third:
            return
        if party.read(third) != b"":
            return
        while True:
            raw = link.recv(None)
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
        self.binding = SimpleNamespace(provider="synthetic", account="synthetic")
        self.refs: list[str] = []

    def cookies(self, auth_ref: str) -> str:
        self.refs.append(auth_ref)
        if self.jar is None:
            raise CoreError(ErrorCode.AUTH_REQUIRED, "no jar")
        if not isinstance(self.jar, str):
            raise CoreError(ErrorCode.AUTH_REQUIRED, "bad jar")
        return self.jar


class Guard:
    def __init__(
        self,
        log: list[str] | None = None,
        *,
        result: CheckedURL | None | object = None,
        addresses: tuple[str, ...] = (PEER,),
        peer_reject: str | None = None,
    ) -> None:
        self.log = log
        self.result = result
        self.addresses = addresses
        self.peer_reject = peer_reject
        self.return_none = False
        self.boom = False
        self.seen: list[tuple[str, str]] = []
        self.peers: list[str] = []

    def check(self, url: str, *, purpose: str) -> CheckedURL:
        self.seen.append((purpose, url))
        if self.log is not None:
            self.log.append("guard:" + purpose)
        if self.boom:
            raise RuntimeError(f"guard failed auth_token={REVIEW_SECRET}")
        if self.return_none:
            return None  # type: ignore[return-value]
        if self.result is not None:
            return self.result  # type: ignore[return-value]
        return checked_url(url, self.addresses)

    def verify_peer(self, checked: CheckedURL, peer_address: str) -> None:
        self.peers.append(peer_address)
        if self.peer_reject is not None and peer_address == self.peer_reject:
            raise RuntimeError(f"peer rejected auth_token={REVIEW_SECRET}")
        if peer_address not in checked.addresses:
            raise RuntimeError(f"peer {peer_address} auth_token={REVIEW_SECRET}")


class FakeHttp:
    def __init__(
        self,
        responses: dict | None = None,
        log: list[str] | None = None,
    ) -> None:
        self.responses = responses or {}
        self.calls: list[dict] = []
        self.log = log

    def request(self, method, url, *, headers, body, checked, timeout):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "body": body,
                "checked": checked,
                "timeout": timeout,
            }
        )
        if self.log is not None:
            self.log.append("http:" + urlparse(url).path)
        path = urlparse(url).path
        if path in self.responses:
            return self.responses[path]
        if path == "/api/auth/check":
            return HttpResponse(
                200,
                json.dumps(
                    {
                        "ok": True,
                        "access_token": SECRET,
                        "viewer_id": "viewer-fixture",
                        "session_binding_id": "binding-fixture",
                    }
                ).encode(),
                {},
                PEER,
            )
        if path == "/api/session":
            return HttpResponse(
                200,
                json.dumps(
                    {
                        "status": "assigned",
                        "vm_id": "vm1",
                        "vm_name": "vm1",
                        "endpoint_url": "wss://vm1.metaaivm.com/",
                        "vm_type": "hatch_vm",
                        "vm_state": "RUNNING",
                    }
                ).encode(),
                {},
                PEER,
            )
        if path == "/api/hatch/token":
            return HttpResponse(200, json.dumps({"token": SECRET}).encode(), {}, PEER)
        if path == "/api/hatch/vm/wake":
            return HttpResponse(200, b"{}", {}, PEER)
        raise AssertionError(path)


class FakeConnector:
    def __init__(self, handler, log: list[str] | None = None, factory=None) -> None:
        self.handler = handler
        self.log = log
        self.factory = factory
        self.urls: list[str] = []
        self.checked: list[CheckedURL] = []
        self.errors: list[BaseException] = []
        self.threads: list[threading.Thread] = []
        self.thread: threading.Thread | None = None

    def connect(self, url, *, checked, timeout):
        self.urls.append(url)
        self.checked.append(checked)
        if self.log is not None:
            self.log.append("ws")
        client, server = duplex()
        if self.factory is not None:
            client = self.factory(client)
        self.thread = threading.Thread(
            target=serve, args=(server, self.handler, self.errors), daemon=True
        )
        self.threads.append(self.thread)
        self.thread.start()
        return client


class FailAfter:
    def __init__(self, inner: MemoryEndpoint, succeed: int) -> None:
        self.inner = inner
        self.left = succeed
        self.peer_address = inner.peer_address

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
    store = kwargs.pop("store", None)
    if store is None:
        store = Store(
            WorkspacePaths.resolve(Path(os.environ["METAMUSE_TEST_STORE_ROOT"]) / str(uuid.uuid4()))
        )
    transport = MuseTransport(
        store=store,
        sleep=kwargs.pop("sleep", None),
        wall_clock=kwargs.pop("wall_clock", None),
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


def test_lifecycle_declares_the_four_host_tools():
    assert IMPLEMENTED_TOOLS == frozenset(
        {
            "metamuse_muse_open",
            "metamuse_muse_send",
            "metamuse_muse_read",
            "metamuse_muse_close_exchange",
        }
    )
    register_operations(None)
    from metamuse.jobs import OPERATIONS

    assert {"muse.open", "muse.send", "muse.history", "muse.sessions"} <= OPERATIONS.keys()


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
        guarded.chat_stream(mutation("chat.stream", {"text": "hello"}), authorization=Token())
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
        transport.chat_stream(mutation("chat.stream", {"text": "hello"}), authorization=None)
    assert caught.value.detail.code == ErrorCode.SCOPE_REQUIRED
    assert "before_external_action" in (caught.value.detail.next_action or "")
    assert http.calls == []
    assert connector.urls == []


def test_handshake_and_serial_calls_use_monotonic_stream_ids():
    frames: list = []
    log: list[str] = []
    transport, http, guard, connector = build(
        handler=ok_frames(frames),
        log=log,
        guard=Guard(log=log),
    )
    with transport:
        started = transport.session_start(
            mutation("session.start", {"title": "topic"}, marker="start-1"),
            authorization=Token(),
        )
        sent = transport.chat_stream(
            mutation("chat.stream", {"text": "hello muse"}, marker="send-1"),
            authorization=Token(),
        )
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
        "hatch.wake",
        "wss.noise",
    ]
    assert log == [
        "guard:auth.check",
        "http:/api/auth/check",
        "guard:session",
        "http:/api/session",
        "guard:hatch.token",
        "http:/api/hatch/token",
        "guard:hatch.wake",
        "http:/api/hatch/vm/wake",
        "guard:wss.noise",
        "ws",
    ]
    assert http.calls[0]["headers"]["Cookie"] == COOKIE
    assert "Authorization" not in http.calls[0]["headers"]
    assert http.calls[2]["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert http.calls[0]["checked"].hostname == "muse.ai"
    assert http.calls[0]["checked"].addresses == (PEER,)
    assert connector.checked[0].hostname == "hatch.metaaivm.com"
    assert connector.checked[0].url.startswith("wss://")
    hatch = json.loads(http.calls[2]["body"])
    assert hatch["vmAddress"] == VM_ADDRESS_TEMPLATE.format(vm_id="vm1")
    assert connector.urls[0].startswith(HATCH_WSS_BASE + "?vm_id=vm1&auth_token=")
    assert "vm1.metaaivm.com" not in connector.urls[0]
    assert len(http.calls) == 4
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
    assert frames[0].request.body == b""
    assert "transcript_mode" not in frames[1].request.path
    assert "session_id=sess-b" in frames[1].request.path
    assert frames[1].request.body == b""


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
            transport.session_start(
                mutation("session.start", {"title": "topic"}),
                authorization=Token(),
                timeout_s=0,
            )
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
        silent = transport.chat_stream(
            mutation("chat.stream", {"text": "hello muse"}),
            authorization=Token(),
            timeout_s=0,
        )
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
            mutation("chat.stream", {"text": "hello muse"}, marker="req-1"),
            authorization=Token(),
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
        attempt = transport.chat_stream(
            mutation("chat.stream", {"text": "hello muse"}),
            authorization=Token(),
        )
    connector.thread.join(2)
    assert connector.errors == []
    assert attempt.bytes_handed_off is True
    assert attempt.evidence.verdict == EVIDENCE_APP_ERROR
    assert attempt.state == SEND_UNCERTAIN
    assert len(frames) == 1
    assert_clean(attempt.evidence.detail + attempt.body.decode() + repr(attempt))
    assert "supersecret" not in attempt.evidence.detail
    assert "REDACTED" in attempt.evidence.detail or "redacted" in attempt.evidence.detail

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
            mutation("chat.stream", {"text": "hello muse"}, marker="req-1"),
            authorization=Token(),
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
        attempt = transport.chat_stream(
            mutation("chat.stream", {"text": "hello muse"}),
            authorization=Token(),
        )
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
            transport.chat_stream(
                mutation("chat.stream", {"text": "hello"}),
                authorization=Token(),
            )
        connector.thread.join(2)
        assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH, name
        assert caught.value.detail.retry_class == "never"
        assert "Do not resend" in (caught.value.detail.next_action or "")
        assert len(frames) == 1, name
        assert_clean(str(caught.value))


def test_nonempty_handshake_payload_is_protocol_mismatch():
    class Attested:
        urls: list[str] = []

        def connect(self, url, *, checked, timeout):
            self.urls.append(url)
            client, server = duplex()

            def run() -> None:
                party = NoiseParty("responder")
                first = server.recv(5)
                party.read(first or b"")
                server.send(bytes(party.conn.write_message(b"attest")))

            threading.Thread(target=run, daemon=True).start()
            return client

    connector = Attested()
    transport, _http, _guard, _connector = build(handler=lambda frame: [], connector=connector)
    with transport, pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert caught.value.detail.retry_class == "never"
    assert "attested" not in str(caught.value)
    assert_no_chain(caught.value)
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
        def request(self, method, url, *, headers, body, checked, timeout):
            if not started.is_set():
                started.set()
                assert release.wait(3)
            return super().request(
                method, url, headers=headers, body=body, checked=checked, timeout=timeout
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
            "/api/auth/check": HttpResponse(
                200,
                json.dumps(
                    {
                        "ok": True,
                        "access_token": SECRET,
                        "viewer_id": "viewer-fixture",
                        "session_binding_id": "binding-fixture",
                    }
                ).encode(),
                {},
                PEER,
            ),
            "/api/session": HttpResponse(200, body, {}, PEER),
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
            HttpResponse(401, secret_body, {}, PEER),
            ErrorCode.AUTH_REQUIRED,
            "user_action",
        ),
        (
            HttpResponse(429, secret_body, {"Retry-After": "12"}, PEER),
            ErrorCode.RATE_LIMITED,
            "safe_read",
        ),
        (
            HttpResponse(
                302, b"", {"Location": f"https://evil.example/?auth_token={SECRET}"}, PEER
            ),
            ErrorCode.SCOPE_REQUIRED,
            "user_action",
        ),
        (
            HttpResponse(503, secret_body, {}, PEER),
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
            "/api/auth/check": HttpResponse(
                200,
                json.dumps(
                    {
                        "ok": True,
                        "access_token": SECRET,
                        "viewer_id": "viewer-fixture",
                        "session_binding_id": "binding-fixture",
                    }
                ).encode(),
                {},
                PEER,
            ),
            "/api/session": HttpResponse(200, b'{"status":"assigned","vm_id":"../evil"}', {}, PEER),
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
        def connect(self, url, *, checked, timeout):
            raise RuntimeError(
                f"failed {url} Authorization: Bearer {SECRET} cookie: hatch_sess={SECRET}"
            )

    transport, _http, _guard, _connector = build(handler=ok_frames([]), connector=Boom())
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.NETWORK_TIMEOUT
    rendered = str(caught.value)
    assert_clean(rendered)
    assert "Bearer" not in rendered
    assert "failed wss" not in rendered
    assert_no_chain(caught.value)
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
            checked=checked_url(MUSE_ORIGIN + "/api/session"),
            timeout=1,
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


def test_curl_recv_with_zero_timeout_does_not_block():
    from curl_cffi.const import CurlECode
    from curl_cffi.curl import CurlError

    class Socket:
        def recv_fragment(self):
            raise CurlError("again", CurlECode.AGAIN)

    started = time.monotonic()
    assert CurlSocket(Socket(), PEER).recv(0) is None
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
        link.push(response_frame(stream_id, 200, b"", end_body=False))
        for _ in range(MAX_COLLECT_FRAMES - 1):
            link.push(chunk_frame(stream_id, b"x", end_body=False))
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
        resolve_entries(checked_url("https://muse.ai/api/session", ()))
    assert caught.value.detail.code == ErrorCode.SCOPE_REQUIRED
