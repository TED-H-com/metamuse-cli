"""Offline spike tests. No network and no Muse account.

    uv run --python 3.12 --with pytest pytest spikes/muse
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from associate import (
    AMBIGUOUS,
    FAILED_BEFORE_SEND,
    NONE,
    REPLY_OBSERVED,
    SEND_UNCERTAIN,
    SENT,
    Exchange,
    associate_reply,
    delivery_state,
    normalize_event,
    observe,
)
from framing import (
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
        "payload": payload,
    }
    event.update(extra)
    return event


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
        _assistant("sess-b", 99, "latest from somewhere else"),
        _assistant("sess-a", 11, "the reply for this exchange"),
        _assistant("sess-a", 12, "background", reply_to_message_id="msg-9"),
    ]
    association = associate_reply(exchange, events)
    assert association.state == REPLY_OBSERVED
    assert association.reply is not None
    assert association.reply["payload"]["display_text"] == "the reply for this exchange"
    assert association.ignored_other_session == 1


def test_only_other_session_does_not_observe_a_reply():
    association = associate_reply(_exchange(), [_assistant("sess-b", 50, "not ours")])
    assert association.state == NONE
    assert association.reply is None
    assert association.ignored_other_session == 1
    assert observe(SEND_UNCERTAIN, association) == SEND_UNCERTAIN


def test_two_candidates_stay_ambiguous():
    events = [
        _assistant("sess-a", 11, "first"),
        _assistant("sess-a", 13, "second"),
    ]
    association = associate_reply(_exchange(), events)
    assert association.state == AMBIGUOUS
    assert association.reply is None
    assert observe(SENT, association) == SENT


def test_missing_status_is_not_a_finished_reply():
    event = _assistant("sess-a", 11, "still writing", payload={"status": ""})
    association = associate_reply(_exchange(), [event])
    assert association.state == NONE


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
    )
    assert state == SENT


def test_late_reply_after_uncertainty_is_observed_once():
    events = [_assistant("sess-a", 11, "arrived late")]
    association = associate_reply(_exchange(), events)
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
    script = ROOT / "handshake.py"
    completed = subprocess.run(
        ["uv", "run", "--python", "3.12", str(script)],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    assert result == {
        "pattern": "Noise_XX_25519_AESGCM_SHA256",
        "handshake_finished": True,
        "empty_payloads": True,
        "roundtrip": "ok",
        "stream_id": 1,
        "library": "noiseprotocol",
        "other_session_rejected": True,
    }
    assert "supersecret" not in completed.stdout
