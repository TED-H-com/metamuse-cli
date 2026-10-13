"""Offline spike tests. No network and no Muse account.

The Noise cases import ``noise`` in-process. They skip when that package is
not already installed. They do not resolve dependencies and they do not call uv.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from associate import (
    AMBIGUOUS,
    CLOSED,
    EVIDENCE_APP_ERROR,
    EVIDENCE_UNDECIDED,
    FAILED_BEFORE_SEND,
    NONE,
    PROGRESS,
    REPLY_OBSERVED,
    SEND_UNCERTAIN,
    SENT,
    UNVERIFIED_UPSTREAM_COMPLETE,
    Exchange,
    associate_reply,
    attempt_evidence,
    delivery_state,
    normalize_event,
    observe,
    user_echo,
)
from framing import (
    MAX_FRAME_BYTES,
    NOISE_MAX_CIPHERTEXT_BYTES,
    NOISE_MAX_PLAINTEXT_BYTES,
    NOISE_TAG_BYTES,
    ApplicationRequest,
    FrameTooLarge,
    IncompleteBody,
    NoiseTransportFrame,
    ProtocolMismatch,
    Reset,
    ServiceFrame,
    decode_client_frame,
    decode_noise_transport_frame,
    decode_server_frame,
    encode_client_frame,
    encode_noise_transport_frame,
    encode_server_frame,
    encode_unknown_kind_frame,
    redact,
)
from loop import FakeGateway, InProcessLink, ReceiveLoop, chunk_frame, response_frame

ROOT = Path(__file__).resolve().parent


def _exchange() -> Exchange:
    return Exchange(
        session_id="sess-a",
        baseline_seq=10,
        sent_text="hello muse",
        request_marker="req-1",
    )


def _assistant(session_id: str, seq: int, text: str, **extra) -> dict:
    payload = {"role": "assistant", "display_text": text, "status": "completed"}
    payload.update(extra.pop("payload", {}))
    event = {
        "session_id": session_id,
        "event_name": "message.assistant",
        "seq": seq,
        "message_id": extra.pop("message_id", f"m-{seq}"),
        "payload": payload,
    }
    event.update(extra)
    return event


def _user(seq: int, text: str = "hello muse", **extra) -> dict:
    payload = {"role": "user", "display_text": text}
    payload.update(extra.pop("payload", {}))
    event = {
        "session_id": "sess-a",
        "event_name": "message.user",
        "seq": seq,
        "message_id": extra.pop("message_id", f"u-{seq}"),
        "payload": payload,
    }
    event.update(extra)
    return event


def _paired(events: list[dict], exchange: Exchange | None = None, **kwargs):
    return associate_reply(
        exchange or _exchange(),
        events,
        complete_statuses=kwargs.pop("complete_statuses", UNVERIFIED_UPSTREAM_COMPLETE),
        **kwargs,
    )


def _run(mode: str, **loop_kwargs):
    link = InProcessLink()
    loop = ReceiveLoop(link, **loop_kwargs)
    gateway = FakeGateway(link, mode)
    with loop:
        stream_id = loop.open_request(verb="POST", path="/chat/stream", body=b"{}")
        gateway.pump_one()
        return stream_id, loop.collect(stream_id)


def test_response_is_correlated_by_stream_id():
    stream_id, (status, body) = _run("decoy-then-match")
    assert stream_id == 1
    assert status == 200
    assert body == b"matched"


def test_chunked_body_succeeds_only_when_end_body_arrives():
    _stream_id, (status, body) = _run("chunked")
    assert status == 200
    assert body == b"AABBCC"


def test_incomplete_body_is_not_success():
    with pytest.raises(IncompleteBody):
        _run("incomplete")


def test_oversized_frame_is_rejected():
    with pytest.raises(FrameTooLarge):
        _run("oversize")


def test_oversized_reassembly_is_rejected_before_success():
    link = InProcessLink()
    loop = ReceiveLoop(link, max_body_bytes=4)
    with loop:
        stream_id = loop.open_request(verb="POST", path="/chat/stream", body=b"{}")
        loop.link.push(response_frame(stream_id, 200, b"AA", end_body=False))
        loop.link.push(chunk_frame(stream_id, b"BBB", end_body=True))
        with pytest.raises(FrameTooLarge):
            loop.collect(stream_id)


def test_unknown_frame_type_is_protocol_mismatch():
    link = InProcessLink()
    loop = ReceiveLoop(link)
    with loop:
        stream_id = loop.open_request(verb="GET", path="/chat/history", body=b"")
        loop.link.push(encode_unknown_kind_frame(stream_id))
        with pytest.raises(ProtocolMismatch, match="unknown frame type"):
            loop.collect(stream_id)


def test_multichunk_transport_frame_is_not_success():
    frame = NoiseTransportFrame(chunk_id=1, chunk_index=0, total_chunks=2, payload=b"half")
    with pytest.raises(ProtocolMismatch, match="multi-chunk"):
        encode_noise_transport_frame(frame)
    raw = b"".join(
        [
            # field 1 chunk_id = 1, field 2 index = 0, field 3 total = 2, field 4 payload
            bytes([0x08, 0x01, 0x10, 0x00, 0x18, 0x02, 0x22, 0x04]),
            b"half",
        ]
    )
    with pytest.raises(ProtocolMismatch, match="multi-chunk"):
        decode_server_frame(raw)


def test_second_consumer_is_rejected():
    loop = ReceiveLoop(InProcessLink())
    with loop, pytest.raises(ProtocolMismatch, match="second receive consumer"), loop:
        pass


def test_second_loop_on_the_same_link_is_rejected():
    link = InProcessLink()
    first = ReceiveLoop(link)
    second = ReceiveLoop(link)
    with first:
        with pytest.raises(ProtocolMismatch, match="second receive consumer"), second:
            pass
        stream_id = first.open_request(verb="POST", path="/chat/stream", body=b"{}")
        link.push(response_frame(stream_id, 200, b'{"ok":true}', end_body=True))
        status, body = first.collect(stream_id)
    assert status == 200
    assert body == b'{"ok":true}'


def test_collect_without_ownership_is_rejected():
    loop = ReceiveLoop(InProcessLink())
    with pytest.raises(ProtocolMismatch, match="ownership"):
        loop.collect(1)


def test_concurrent_collect_is_rejected():
    link = InProcessLink()
    loop = ReceiveLoop(link)
    inside = threading.Event()
    release = threading.Event()
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

    def intruder() -> None:
        assert inside.wait(2)
        try:
            loop.collect(1)
        except ProtocolMismatch as exc:
            errors.append(str(exc))
        finally:
            release.set()

    owner_thread = threading.Thread(target=owner)
    intruder_thread = threading.Thread(target=intruder)
    owner_thread.start()
    intruder_thread.start()
    intruder_thread.join(3)
    owner_thread.join(3)
    assert not owner_thread.is_alive()
    assert errors
    assert "concurrent" in errors[0]


def test_frame_budget_exhaustion_is_incomplete():
    link = InProcessLink()
    loop = ReceiveLoop(link)
    with loop:
        stream_id = loop.open_request(verb="POST", path="/chat/stream", body=b"{}")
        for _ in range(10):
            link.push(chunk_frame(stream_id, b"", end_body=False))
        with pytest.raises(IncompleteBody, match="frame budget"):
            loop.collect(stream_id, max_frames=3, timeout_s=30)


def test_deadline_exhaustion_is_incomplete():
    link = InProcessLink()
    loop = ReceiveLoop(link, clock=lambda: 5.0)
    with loop:
        stream_id = loop.open_request(verb="POST", path="/chat/stream", body=b"{}")
        link.push(response_frame(stream_id, 200, b'{"ok":true}', end_body=True))
        with pytest.raises(IncompleteBody, match="deadline"):
            loop.collect(stream_id, timeout_s=0)


def test_reset_is_not_success():
    link = InProcessLink()
    loop = ReceiveLoop(link)
    with loop:
        stream_id = loop.open_request(verb="POST", path="/chat/stream", body=b"{}")
        loop.link.push(
            encode_server_frame(
                ServiceFrame(stream_id=stream_id, kind="reset", reset=Reset(3, "nope"))
            )
        )
        with pytest.raises(ProtocolMismatch, match="stream reset"):
            loop.collect(stream_id)


def test_negative_chunk_id_roundtrip():
    frame = ServiceFrame(
        stream_id=4,
        kind="request",
        request=ApplicationRequest(verb="GET", path="/chat/history", body=b"", end_body=True),
    )
    raw = encode_client_frame(0, frame, chunk_id=-99)
    outer = decode_noise_transport_frame(raw)
    assert outer.chunk_id == -99
    service, decoded = decode_client_frame(raw)
    assert service == 0
    assert decoded.stream_id == 4
    assert decoded.request is not None
    assert decoded.request.path == "/chat/history"


def test_other_session_reply_is_not_associated():
    exchange = _exchange()
    events = [
        _user(11),
        _assistant("sess-b", 99, "latest from somewhere else"),
        _assistant("sess-a", 12, "the reply for this exchange", message_id="ours"),
        _assistant("sess-a", 13, "background", reply_to_message_id="msg-9"),
    ]
    association = _paired(events, exchange)
    assert association.state == REPLY_OBSERVED
    assert association.reply is not None
    assert association.reply["payload"]["display_text"] == "the reply for this exchange"
    assert association.ignored_other_session == 1


def test_only_other_session_does_not_observe_a_reply():
    association = _paired([_assistant("sess-b", 50, "not ours")])
    assert association.state == NONE
    assert association.reply is None
    assert association.ignored_other_session == 1
    assert observe(SEND_UNCERTAIN, association) == SEND_UNCERTAIN


def test_late_answer_without_our_turn_is_not_paired():
    late = _assistant("sess-a", 11, "answer to the previous question")
    association = _paired([late])
    assert association.state == NONE
    assert association.reply is None
    assert association.unresolved
    assert observe(SENT, association) == SENT


def test_manual_user_message_blocks_the_following_assistant():
    events = [
        _user(11),
        _user(12, "unrelated manual question", message_id="manual"),
        _assistant("sess-a", 13, "answer to the manual question", message_id="a-13"),
    ]
    association = _paired(events)
    assert association.state == NONE
    assert association.reply is None
    assert any(event["message_id"] == "a-13" for event in association.unresolved)
    shortened = _paired(
        [
            _user(11),
            _assistant("sess-a", 13, "answer to the manual question", message_id="a-13"),
        ],
        prior=association,
    )
    assert shortened.state != REPLY_OBSERVED
    assert shortened.reply is None


def test_user_turn_before_the_echo_is_not_auto_paired():
    events = [
        _user(11, "an earlier question that was still open", message_id="earlier"),
        _user(12),
        _assistant("sess-a", 13, "could be either turn", message_id="a-13"),
    ]
    association = _paired(events)
    assert association.state == AMBIGUOUS
    assert association.reply is None
    assert observe(SENT, association) == SENT


def test_request_marker_match_is_exact():
    exchange = _exchange()
    prefix = _user(11, "some other text", **{"x-request-id": "req-10"})
    assert user_echo(exchange, [prefix]) is None
    exact = _user(12, "some other text", payload={"client_request_id": "req-1"})
    assert user_echo(exchange, [exact]) is exact
    embedded = _user(13, "please see req-1 in this sentence")
    assert user_echo(exchange, [embedded]) is None


def test_two_candidates_stay_ambiguous_across_polls():
    first_reply = _assistant("sess-a", 12, "first", message_id="m-12")
    second_reply = _assistant("sess-a", 13, "second", message_id="m-13")
    association = _paired([_user(11), first_reply, second_reply])
    assert association.state == AMBIGUOUS
    assert association.reply is None
    assert observe(SENT, association) == SENT
    shortened = _paired([_user(11), first_reply], prior=association)
    assert shortened.state == AMBIGUOUS
    assert shortened.reply is None
    resolved = _paired([], prior=shortened, host_decision="m-12")
    assert resolved.state == REPLY_OBSERVED
    assert resolved.reply is not None
    assert resolved.reply["message_id"] == "m-12"


def test_conflicting_marker_beats_identical_text():
    exchange = _exchange()
    conflict = _user(11, **{"x-request-id": "req-2"})
    assert conflict["payload"]["display_text"] == exchange.sent_text
    assert user_echo(exchange, [conflict]) is None
    association = _paired([conflict, _assistant("sess-a", 12, "not our answer", message_id="m-12")])
    assert association.state == NONE
    assert association.reply is None


def test_outstanding_earlier_exchange_keeps_the_late_answer_unpaired():
    late = _assistant("sess-a", 12, "answer to the previous question", message_id="old")
    events = [_user(11), late]
    held = Exchange(
        session_id="sess-a",
        baseline_seq=10,
        sent_text="hello muse",
        request_marker="req-1",
        earlier_unresolved=True,
    )
    echo_only = _paired([_user(11)], held)
    assert echo_only.state == NONE
    arrived = _paired(events, held, prior=echo_only)
    assert arrived.state == AMBIGUOUS
    assert arrived.reply is None
    named = _paired(events, held, host_decision="old")
    assert named.state == REPLY_OBSERVED
    assert named.reply is not None
    assert named.reply["message_id"] == "old"
    released = _paired(events, _exchange(), prior=arrived)
    assert released.state == REPLY_OBSERVED
    assert released.reply is not None
    assert released.reply["message_id"] == "old"


def test_unrelated_disqualification_does_not_resolve_ambiguity():
    first = _assistant("sess-a", 12, "first", message_id="m-12")
    second = _assistant("sess-a", 13, "second", message_id="m-13")
    background = _assistant(
        "sess-a",
        14,
        "background",
        message_id="m-14",
        payload={"status": "running"},
    )
    association = _paired([_user(11), first, second, background])
    assert association.state == AMBIGUOUS
    second_running = _assistant(
        "sess-a",
        13,
        "second",
        message_id="m-13",
        payload={"status": "running"},
    )
    background_linked = _assistant(
        "sess-a",
        14,
        "background",
        message_id="m-14",
        payload={"status": "running"},
        reply_to_message_id="some-other-turn",
    )
    later = _paired(
        [_user(11), first, second_running, background_linked],
        prior=association,
    )
    assert later.state == AMBIGUOUS
    assert later.reply is None
    assert any(event.get("message_id") == "m-12" for event in later.unresolved)


def test_observe_spans_association_and_exchange_state():
    first = _assistant("sess-a", 12, "first", message_id="m-12")
    second = _assistant("sess-a", 13, "second", message_id="m-13")
    ambiguous = _paired([_user(11), first, second])
    assert ambiguous.state == AMBIGUOUS
    assert observe(SENT, ambiguous) == SENT
    assert observe(SEND_UNCERTAIN, ambiguous) == SEND_UNCERTAIN
    assert observe(AMBIGUOUS, ambiguous) == AMBIGUOUS
    resolved = _paired([], prior=ambiguous, host_decision="m-12")
    assert resolved.state == REPLY_OBSERVED
    assert observe(AMBIGUOUS, resolved) == REPLY_OBSERVED
    assert observe(SENT, resolved) == REPLY_OBSERVED
    assert observe(SEND_UNCERTAIN, resolved) == REPLY_OBSERVED
    assert observe(CLOSED, ambiguous) == CLOSED
    assert observe(CLOSED, resolved) == CLOSED
    assert observe(REPLY_OBSERVED, ambiguous) == REPLY_OBSERVED
    assert observe(FAILED_BEFORE_SEND, resolved) == FAILED_BEFORE_SEND


def test_message_id_arrival_enriches_the_same_event():
    user = _user(11)
    user.pop("message_id")
    assistant = _assistant("sess-a", 12, "the reply", message_id="m-12")
    first = _paired([user, assistant])
    assert first.state == REPLY_OBSERVED
    enriched = _paired([_user(11, message_id="u-11"), assistant], prior=first)
    assert enriched.state == REPLY_OBSERVED
    assert enriched.reply is not None
    assert enriched.reply["message_id"] == "m-12"
    user_rows = [
        event
        for event in enriched.memory.events.values()
        if event.get("event_name") == "message.user"
    ]
    assert len(user_rows) == 1
    assert user_rows[0]["message_id"] == "u-11"


def test_different_message_ids_at_one_seq_stay_distinct():
    ours = _user(11, message_id="u-a")
    assistant = _assistant("sess-a", 12, "the reply", message_id="m-12")
    first = _paired([ours, assistant])
    assert first.state == REPLY_OBSERVED
    other = _user(11, "a different question", message_id="u-b")
    second = _paired([other], prior=first)
    assert second.state == REPLY_OBSERVED
    assert second.reply is not None
    assert second.reply["message_id"] == "m-12"
    user_rows = [
        event
        for event in second.memory.events.values()
        if event.get("event_name") == "message.user"
    ]
    assert len(user_rows) == 2


def test_positive_disqualification_can_resolve_ambiguity():
    first_reply = _assistant("sess-a", 12, "first", message_id="m-12")
    second_reply = _assistant("sess-a", 13, "second", message_id="m-13")
    association = _paired([_user(11), first_reply, second_reply])
    assert association.state == AMBIGUOUS
    disqualified = dict(second_reply)
    disqualified["reply_to_message_id"] = "earlier-turn"
    resolved = _paired([disqualified], prior=association)
    assert resolved.state == REPLY_OBSERVED
    assert resolved.reply is not None
    assert resolved.reply["message_id"] == "m-12"


def test_missing_status_is_not_a_finished_reply():
    event = _assistant("sess-a", 12, "still writing", payload={"status": ""})
    association = _paired([_user(11), event])
    assert association.state == NONE
    assert association.reply is None


def test_uncalibrated_status_is_progress():
    running = _assistant("sess-a", 12, "partial text", payload={"status": "running"})
    association = _paired([_user(11), running])
    assert association.state == PROGRESS
    assert association.reply is None
    assert observe(SENT, association) == SENT
    named_but_uncalibrated = _assistant("sess-a", 12, "looks finished")
    default_set = associate_reply(_exchange(), [_user(11), named_but_uncalibrated])
    assert default_set.state == PROGRESS
    assert default_set.reply is None


def test_event_time_before_submit_is_not_this_attempt():
    exchange = Exchange(
        session_id="sess-a",
        baseline_seq=10,
        sent_text="hello muse",
        request_marker="req-1",
        submitted_at_ms=1_000,
    )
    echo = _user(11, time_ms=1_100)
    stale = _assistant("sess-a", 12, "finished earlier", time_ms=900)
    association = _paired([echo, stale], exchange)
    assert association.state != REPLY_OBSERVED
    assert association.reply is None
    stale_echo = _user(11, time_ms=500)
    assert user_echo(exchange, [stale_echo]) is None


def test_user_echo_marks_sent_when_the_stream_times_out():
    exchange = _exchange()
    echo = {
        "session_id": "sess-a",
        "event_name": "message.user",
        "seq": 11,
        "payload": {"display_text": "hello muse", "role": "user"},
    }
    assert (
        delivery_state(
            wrote_any_bytes=True,
            stream_complete=False,
            stream_status=None,
            exchange=exchange,
            events=[echo],
        )
        == SENT
    )


def test_timeout_without_echo_is_send_uncertain():
    state = delivery_state(
        wrote_any_bytes=True,
        stream_complete=False,
        stream_status=None,
        exchange=_exchange(),
        events=[_assistant("sess-b", 40, "irrelevant")],
    )
    assert state == SEND_UNCERTAIN


def test_no_bytes_written_is_failed_before_send():
    state = delivery_state(
        wrote_any_bytes=False,
        stream_complete=False,
        stream_status=None,
        exchange=_exchange(),
        events=[_assistant("sess-a", 11, "should not count")],
    )
    assert state == FAILED_BEFORE_SEND
    assert observe(state, associate_reply(_exchange(), [])) == FAILED_BEFORE_SEND


def test_finished_stream_response_is_sent_without_a_user_echo():
    state = delivery_state(
        wrote_any_bytes=True,
        stream_complete=True,
        stream_status=200,
        exchange=_exchange(),
        events=[],
        body=b'{"ok":true}',
    )
    assert state == SENT


def test_app_error_stays_uncertain_until_this_echo():
    body = b'{"ok":false,"error":"rejected"}'
    evidence = attempt_evidence(stream_complete=True, stream_status=200, body=body)
    assert evidence.verdict == EVIDENCE_APP_ERROR
    assert evidence.detail == "ok false: rejected"
    echo = _user(11)
    uncertain = delivery_state(
        wrote_any_bytes=True,
        stream_complete=True,
        stream_status=200,
        exchange=_exchange(),
        events=[],
        body=body,
    )
    assert uncertain == SEND_UNCERTAIN
    sent = delivery_state(
        wrote_any_bytes=True,
        stream_complete=True,
        stream_status=200,
        exchange=_exchange(),
        events=[echo],
        body=body,
    )
    assert sent == SENT
    assert sent != FAILED_BEFORE_SEND
    reply = _assistant("sess-a", 12, "the reply", message_id="m-12")
    association = _paired([echo, reply])
    assert association.state == REPLY_OBSERVED
    assert observe(uncertain, association) == REPLY_OBSERVED
    assert observe(sent, association) == REPLY_OBSERVED
    refused = attempt_evidence(stream_complete=True, stream_status=500, body=b"\xff")
    assert refused.verdict == EVIDENCE_APP_ERROR
    non_2xx = delivery_state(
        wrote_any_bytes=True,
        stream_complete=True,
        stream_status=500,
        exchange=_exchange(),
        events=[],
        body=b"nope",
    )
    assert non_2xx == SEND_UNCERTAIN
    promoted = delivery_state(
        wrote_any_bytes=True,
        stream_complete=True,
        stream_status=500,
        exchange=_exchange(),
        events=[echo],
        body=b"nope",
    )
    assert promoted == SENT
    assert (
        attempt_evidence(stream_complete=True, stream_status=500, body=b"nope").verdict
        == EVIDENCE_APP_ERROR
    )


def test_non_json_2xx_does_not_confirm_delivery():
    state = delivery_state(
        wrote_any_bytes=True,
        stream_complete=True,
        stream_status=200,
        exchange=_exchange(),
        events=[],
        body=b"not json",
    )
    assert state == SEND_UNCERTAIN
    evidence = attempt_evidence(stream_complete=True, stream_status=200, body=b"not json")
    assert evidence.verdict == EVIDENCE_UNDECIDED


def test_undecodable_body_stays_send_uncertain():
    body = b"\xff"
    evidence = attempt_evidence(stream_complete=True, stream_status=200, body=body)
    assert evidence.verdict == EVIDENCE_UNDECIDED
    state = delivery_state(
        wrote_any_bytes=True,
        stream_complete=True,
        stream_status=200,
        exchange=_exchange(),
        events=[],
        body=body,
    )
    assert state == SEND_UNCERTAIN


def test_late_reply_after_uncertainty_is_observed_once():
    events = [_user(11), _assistant("sess-a", 12, "arrived late")]
    association = _paired(events)
    assert observe(SEND_UNCERTAIN, association) == REPLY_OBSERVED


def test_query_scope_does_not_overwrite_a_foreign_session_id():
    event = normalize_event({"event_name": "message.assistant", "session_id": "sess-b"}, "sess-a")
    assert event["session_id"] == "sess-b"
    inherited = normalize_event({"event_name": "message.user", "seq": 1}, "sess-a")
    assert inherited["session_id"] == "sess-a"


def test_diagnostics_redact_query_tokens():
    message = redact("wss://gateway.example/v1/noise?vm_id=1&auth_token=supersecret")
    assert "supersecret" not in message
    assert "auth_token=[redacted]" in message
    error = ProtocolMismatch("Authorization: Bearer supersecret")
    assert "supersecret" not in str(error)


def test_spike_sources_do_not_dial_out():
    for name in ("framing.py", "loop.py", "associate.py", "handshake.py"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "import socket" not in text
        assert "curl_cffi" not in text
        assert "hatch.metaaivm.com" not in text
        assert "muse.ai" not in text


def test_noise_xx_handshake_against_fake_gateway():
    pytest.importorskip("noise")
    from handshake import handshake

    result = handshake()
    assert result == {
        "pattern": "Noise_XX_25519_AESGCM_SHA256",
        "handshake_finished": True,
        "empty_payloads": True,
        "roundtrip": "ok",
        "stream_id": 1,
        "library": "noiseprotocol",
        "other_session_rejected": True,
    }


def test_noise_ciphertext_budget_boundary():
    """65519 plaintext round-trips; 65520 ciphertext is 65536 and is rejected.

    noiseprotocol 0.3.1 ``encrypt`` allows plaintext up to 65535. The peer
    then rejects the ciphertext because ``decrypt`` enforces ``MAX_MESSAGE_LEN``.
    """
    pytest.importorskip("noise")
    from handshake import open_transport
    from noise.constants import MAX_MESSAGE_LEN
    from noise.exceptions import NoiseInvalidMessage

    assert MAX_MESSAGE_LEN == NOISE_MAX_CIPHERTEXT_BYTES == 65535
    assert NOISE_TAG_BYTES == 16
    assert NOISE_MAX_PLAINTEXT_BYTES == 65519
    assert MAX_FRAME_BYTES == NOISE_MAX_PLAINTEXT_BYTES

    client, gateway = open_transport()
    accepted = b"x" * NOISE_MAX_PLAINTEXT_BYTES
    ciphertext = bytes(client.encrypt(accepted))
    assert len(ciphertext) == NOISE_MAX_CIPHERTEXT_BYTES
    assert len(ciphertext) == len(accepted) + NOISE_TAG_BYTES
    assert bytes(gateway.decrypt(ciphertext)) == accepted

    client, gateway = open_transport()
    oversized = b"y" * (NOISE_MAX_PLAINTEXT_BYTES + 1)
    rejected = bytes(client.encrypt(oversized))
    assert len(rejected) == NOISE_MAX_CIPHERTEXT_BYTES + 1
    with pytest.raises(NoiseInvalidMessage):
        gateway.decrypt(rejected)
