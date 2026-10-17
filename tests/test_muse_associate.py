"""Ported association rules. Offline. No sockets.

The reviewed rules live in ``spikes/muse/associate.py``. These tests call the
production functions re-exported from ``metamuse.muse``.
"""

from __future__ import annotations

from metamuse.muse import (
    AMBIGUOUS,
    CALIBRATED_COMPLETE_STATUSES,
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


def test_calibrated_complete_statuses_stay_empty():
    assert CALIBRATED_COMPLETE_STATUSES == frozenset()


def test_native_user_echo_uses_exact_content_before_display_text():
    exchange = _exchange()
    event = _user(11, "presentation text", payload={"content": exchange.sent_text})
    assert user_echo(exchange, [event]) == event
    association = _paired([event, _assistant("sess-a", 12, "reply")], exchange)
    assert association.state == REPLY_OBSERVED


def test_matching_display_text_does_not_override_different_user_content_or_marker():
    exchange = _exchange()
    different = _user(11, exchange.sent_text, payload={"content": "another question"})
    assert user_echo(exchange, [different]) is None
    conflict = _user(
        11, "presentation text", payload={"content": exchange.sent_text, "request_marker": "req-2"}
    )
    assert user_echo(exchange, [conflict]) is None


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


def test_m7_contradictory_markers_do_not_prove_delivery():
    exchange = _exchange()
    event = _user(
        11,
        request_marker="req-1",
        payload={"client_request_id": "req-2", "display_text": exchange.sent_text},
    )
    assert user_echo(exchange, [event]) is None
    state = delivery_state(
        wrote_any_bytes=True,
        stream_complete=False,
        stream_status=None,
        exchange=exchange,
        events=[event],
        body=None,
    )
    assert state == SEND_UNCERTAIN


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
