"""Map gateway evidence onto the SPEC §6 exchange states.

prepared → submitting → sent | send_uncertain | send_rejected | failed_before_send
sent / send_uncertain → reply_observed | ambiguous → closed

`closed` is a host settlement (answered / needs_more_data / abandoned). No
gateway frame produces it. This module never resends.

Provenance
----------
Restates, then changes, reply filtering from nikships/muse-cli (MIT,
Copyright (c) 2026 Nik Ships), commit
ebcb6310432ad2d39927bcb59e686330b0538310:

- ``src/muse_cli/cli.py`` ``is_reply`` (assistant event, seq above baseline,
  empty ``reply_to_message_id``, non-empty text)
- ``src/muse_cli/cli.py`` ``fmt_event`` (text is ``display_text``, else
  ``content``)
- ``src/muse_cli/chat.py`` ``Chat.new_replies`` (same discriminator)
- ``src/muse_cli/gateway.py`` ``call_json`` (a JSON object with ``ok is False``
  is an application error, not success)

The copyright line and the MIT permission notice are copied in
``THIRD_PARTY_NOTICES.md``. This file is not a paste of those functions.

What changed:

- A candidate must follow this exchange's own user turn. The turn is an
  exact text match or an exact request-marker match. ``req-1`` does not
  match ``req-10``.
- A late answer with no such turn, a manual user message between the turn
  and the assistant, or another user turn still open before the echo, is
  not paired. Unresolved events are retained.
- When the event carries ``time_ms``, a stamp earlier than the local submit
  time is not this attempt. muse-cli does not read that field.
- Observations accumulate and de-duplicate. Ambiguity stays until a later
  observation of the same event positively disqualifies a candidate (a
  ``reply_to_message_id`` appears) or the host names one message id. A
  shorter later poll does not resolve it.
- Completion requires a status in an explicit calibrated set. The default
  set is empty. ``running`` and any other unknown value are progress. The
  upstream truthy check is not adopted. The comment in ``is_reply`` names
  ``completed``; that word is not a LIVE-verified enum.
- A finished HTTP 2xx body whose JSON ``ok`` is false is ``send_rejected``.
  A non-2xx finished status is the same. Neither one is delivery, and
  neither one is retried.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

PREPARED = "prepared"
SUBMITTING = "submitting"
SENT = "sent"
SEND_UNCERTAIN = "send_uncertain"
SEND_REJECTED = "send_rejected"
FAILED_BEFORE_SEND = "failed_before_send"
REPLY_OBSERVED = "reply_observed"
CLOSED = "closed"
AMBIGUOUS = "ambiguous"
PROGRESS = "progress"
NONE = "none"

# Upstream cli.py says to wait for "completed" and then accepts any truthy
# status. LIVE has not listed the enum. B09 must not treat that word as
# calibrated until a session records it. Tests pass the set explicitly when
# they need to exercise the completion gate.
UNVERIFIED_UPSTREAM_COMPLETE = frozenset({"completed"})
CALIBRATED_COMPLETE_STATUSES: frozenset[str] = frozenset()

_MARKER_KEYS = ("request_marker", "x-request-id", "client_request_id")


@dataclass(frozen=True)
class Exchange:
    session_id: str
    baseline_seq: int
    sent_text: str
    request_marker: str
    submitted_at_ms: int | None = None


@dataclass
class ObservationMemory:
    """Cumulative history for one exchange. A later poll must pass this back."""

    events: dict[tuple, dict] = field(default_factory=dict)
    ambiguous: bool = False
    eliminated: set[tuple] = field(default_factory=set)

    def note(self, events: list[dict]) -> None:
        for event in events:
            key = _event_key(event)
            prev = self.events.get(key)
            if prev is None:
                self.events[key] = _copy_event(event)
                continue
            if _reply_to(event) and not _reply_to(prev):
                self.eliminated.add(key)
            self.events[key] = _merge(prev, event)


@dataclass(frozen=True)
class Association:
    state: str
    reply: dict | None
    ignored_other_session: int
    progress: tuple[dict, ...] = ()
    unresolved: tuple[dict, ...] = ()
    memory: ObservationMemory = field(default_factory=ObservationMemory, compare=False)


def normalize_event(event: dict, query_session_id: str | None) -> dict:
    """Keep an explicit session id. Fill it from the history query only when absent.

    Inference: muse-cli scopes chat.history with a session_id query parameter and
    the events it prints do not repeat that id. An event that names a different
    session must not inherit the query.
    """
    out = dict(event)
    named = out.get("session_id")
    if named in (None, ""):
        out["session_id"] = query_session_id
    return out


def _payload(event: dict) -> dict:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def _copy_event(event: dict) -> dict:
    copied = dict(event)
    payload = copied.get("payload")
    if isinstance(payload, dict):
        copied["payload"] = dict(payload)
    return copied


def _merge(prev: dict, new: dict) -> dict:
    """Fold a later observation in without dropping fields the new view omits."""
    merged = _copy_event(prev)
    for key, value in new.items():
        if key == "payload" and isinstance(value, dict):
            payload = dict(_payload(merged))
            for payload_key, payload_value in value.items():
                if payload_value not in (None, ""):
                    payload[payload_key] = payload_value
            merged["payload"] = payload
        elif value not in (None, ""):
            merged[key] = value
    return merged


def _text(event: dict) -> str:
    payload = _payload(event)
    return str(payload.get("display_text") or payload.get("content") or "")


def _reply_to(event: dict) -> str:
    payload = _payload(event)
    return str(
        event.get("reply_to_message_id") or payload.get("reply_to_message_id") or ""
    )


def _seq(event: dict) -> int:
    return int(event.get("seq") or 0)


def _message_id(event: dict) -> str:
    payload = _payload(event)
    return str(event.get("message_id") or payload.get("message_id") or "")


def _event_key(event: dict) -> tuple:
    return (
        str(event.get("session_id") or ""),
        str(event.get("event_name") or ""),
        _seq(event),
        _message_id(event),
    )


def _event_time_ms(event: dict) -> int | None:
    payload = _payload(event)
    for source in (event, payload):
        if source.get("time_ms") is not None:
            return int(source["time_ms"])
    return None


def _before_submit(exchange: Exchange, event: dict) -> bool:
    if exchange.submitted_at_ms is None:
        return False
    stamp = _event_time_ms(event)
    if stamp is None:
        return False
    return stamp < exchange.submitted_at_ms


def _marker_values(event: dict) -> set[str]:
    found: set[str] = set()
    for source in (event, _payload(event)):
        for key in _MARKER_KEYS:
            value = source.get(key)
            if value in (None, ""):
                continue
            found.add(str(value))
    return found


def _marker_echoed(event: dict, marker: str) -> bool:
    """Exact field match. A prefix of another id is not this request."""
    if not marker:
        return False
    return marker in _marker_values(event)


def _marker_conflict(exchange: Exchange, event: dict) -> bool:
    if not exchange.request_marker:
        return False
    values = _marker_values(event)
    if not values:
        return False
    return exchange.request_marker not in values


def _is_our_echo(exchange: Exchange, event: dict) -> bool:
    if event.get("session_id") != exchange.session_id:
        return False
    if event.get("event_name") != "message.user":
        return False
    if _seq(event) <= exchange.baseline_seq:
        return False
    if _before_submit(exchange, event):
        return False
    if _text(event) == exchange.sent_text:
        return True
    return _marker_echoed(event, exchange.request_marker)


def user_echo(exchange: Exchange, events: list[dict]) -> dict | None:
    """Earliest same-session user event after the baseline that is this send.

    Text must equal the sent text. A request marker must equal a marker field.
    Substring matches are not delivery evidence.
    """
    matches = [event for event in events if _is_our_echo(exchange, event)]
    if not matches:
        return None
    matches.sort(key=_seq)
    return matches[0]


def _status_kind(event: dict, complete_statuses: frozenset[str]) -> str:
    raw = _payload(event).get("status")
    if raw in (None, "", False):
        return "empty"
    if str(raw) in complete_statuses:
        return "complete"
    return "progress"


def _structural_assistant(event: dict) -> bool:
    role = _payload(event).get("role")
    if role not in (None, "", "assistant"):
        return False
    if _reply_to(event):
        return False
    return bool(_text(event))


def _other_user(exchange: Exchange, event: dict, echo: dict) -> bool:
    if event.get("session_id") != exchange.session_id:
        return False
    if event.get("event_name") != "message.user":
        return False
    if _seq(event) <= exchange.baseline_seq:
        return False
    if _event_key(event) == _event_key(echo):
        return False
    return not _is_our_echo(exchange, event)


def _user_between(
    exchange: Exchange, events: list[dict], echo: dict, candidate: dict
) -> bool:
    low = _seq(echo)
    high = _seq(candidate)
    for event in events:
        if not _other_user(exchange, event, echo):
            continue
        seq = _seq(event)
        if low < seq < high:
            return True
    return False


def _earlier_user(exchange: Exchange, events: list[dict], echo: dict) -> bool:
    echo_seq = _seq(echo)
    for event in events:
        if not _other_user(exchange, event, echo):
            continue
        seq = _seq(event)
        if exchange.baseline_seq < seq < echo_seq:
            return True
    return False


def _tied(exchange: Exchange, events: list[dict], echo: dict, event: dict) -> bool:
    if event.get("session_id") != exchange.session_id:
        return False
    if event.get("event_name") != "message.assistant":
        return False
    if _seq(event) <= exchange.baseline_seq or _seq(event) <= _seq(echo):
        return False
    if _before_submit(exchange, event):
        return False
    if _marker_conflict(exchange, event):
        return False
    if _user_between(exchange, events, echo, event):
        return False
    return _structural_assistant(event)


def _same_session_assistants(exchange: Exchange, events: list[dict]) -> list[dict]:
    found = [
        event
        for event in events
        if event.get("session_id") == exchange.session_id
        and event.get("event_name") == "message.assistant"
        and _seq(event) > exchange.baseline_seq
    ]
    found.sort(key=_seq)
    return found


def _match_decision(events: list[dict], decision: str) -> dict | None:
    matches = [event for event in events if _message_id(event) == decision]
    if len(matches) == 1:
        return matches[0]
    return None


def _result(
    state: str,
    reply: dict | None,
    ignored: int,
    progress: list[dict],
    unresolved: list[dict],
    memory: ObservationMemory,
) -> Association:
    return Association(
        state, reply, ignored, tuple(progress), tuple(unresolved), memory
    )


def associate_reply(
    exchange: Exchange,
    events: list[dict],
    *,
    prior: Association | None = None,
    complete_statuses: frozenset[str] | None = None,
    host_decision: str | None = None,
) -> Association:
    """Pair at most one assistant reply with this exchange's user turn.

    Pass the previous return value as ``prior`` on every later poll. The
    memory is the union of observations. Omitting ``prior`` starts over and
    will hide candidates that a short history page no longer shows.
    """
    memory = prior.memory if prior is not None else ObservationMemory()
    memory.note(events)
    world = list(memory.events.values())
    statuses = (
        CALIBRATED_COMPLETE_STATUSES if complete_statuses is None else complete_statuses
    )
    ignored = sum(
        1
        for event in world
        if event.get("session_id") != exchange.session_id
        and event.get("event_name") == "message.assistant"
    )
    echoes = [event for event in world if _is_our_echo(exchange, event)]
    echoes.sort(key=_seq)
    assistants = _same_session_assistants(exchange, world)

    if len(echoes) > 1:
        memory.ambiguous = True
        return _result(AMBIGUOUS, None, ignored, [], assistants, memory)
    if not echoes:
        return _result(NONE, None, ignored, [], assistants, memory)

    echo = echoes[0]
    progress: list[dict] = []
    writing: list[dict] = []
    completes: list[dict] = []
    for event in assistants:
        if not _tied(exchange, world, echo, event):
            if _structural_assistant(event) or _text(event):
                writing.append(event)
            continue
        kind = _status_kind(event, statuses)
        if kind == "complete":
            completes.append(event)
        elif kind == "progress":
            progress.append(event)
        else:
            writing.append(event)

    earlier = _earlier_user(exchange, world, echo)
    if earlier and completes:
        memory.ambiguous = True
        if host_decision is not None:
            chosen = _match_decision(completes, host_decision)
            if chosen is not None:
                memory.ambiguous = False
                return _result(REPLY_OBSERVED, chosen, ignored, progress, [], memory)
        return _result(AMBIGUOUS, None, ignored, progress, completes + writing, memory)

    if len(completes) > 1:
        memory.ambiguous = True

    if host_decision is not None:
        chosen = _match_decision(completes, host_decision)
        if chosen is None:
            if completes:
                memory.ambiguous = True
            state = AMBIGUOUS if memory.ambiguous else NONE
            return _result(state, None, ignored, progress, completes or writing, memory)
        memory.ambiguous = False
        return _result(REPLY_OBSERVED, chosen, ignored, progress, [], memory)

    if memory.ambiguous:
        if len(completes) == 1 and memory.eliminated:
            memory.ambiguous = False
            return _result(REPLY_OBSERVED, completes[0], ignored, progress, [], memory)
        retained = completes if completes else writing
        return _result(AMBIGUOUS, None, ignored, progress, retained, memory)

    if len(completes) == 1:
        return _result(REPLY_OBSERVED, completes[0], ignored, progress, [], memory)
    if len(completes) > 1:
        return _result(AMBIGUOUS, None, ignored, progress, completes, memory)
    if progress:
        return _result(PROGRESS, None, ignored, progress, writing, memory)
    return _result(NONE, None, ignored, [], writing, memory)


def _application_verdict(
    *,
    stream_complete: bool,
    stream_status: int | None,
    body: bytes | None,
) -> str:
    """Return ``acked``, ``rejected``, or ``pending``.

    Mirrors ``Gateway.request`` / ``call_json`` at the pinned muse-cli commit:
    a finished status outside 2xx is an error, and a JSON object with
    ``ok is False`` is an error. An empty or non-object body is not confirmation
    here, which is stricter than treating an empty payload as ``{}``.
    """
    if not stream_complete or stream_status is None:
        return "pending"
    if stream_status < 200 or stream_status >= 300:
        return "rejected"
    if not body:
        return "pending"
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return "pending"
    if not isinstance(payload, dict):
        return "pending"
    if payload.get("ok") is False:
        return "rejected"
    return "acked"


def delivery_state(
    *,
    wrote_any_bytes: bool,
    stream_complete: bool,
    stream_status: int | None,
    exchange: Exchange,
    events: list[dict],
    body: bytes | None = None,
) -> str:
    """Decide sent / uncertain / rejected / failed after a single attempt.

    A finished 2xx JSON object whose ``ok`` is not false is the send
    acknowledgement. A same-session exact user echo is the other positive
    evidence. ``ok: false`` and a non-2xx finished status are rejections:
    bytes were handed off, so this is not ``failed_before_send``, and the
    attempt is not retried. Anything else stays ``send_uncertain``.
    """
    if not wrote_any_bytes:
        return FAILED_BEFORE_SEND
    verdict = _application_verdict(
        stream_complete=stream_complete,
        stream_status=stream_status,
        body=body,
    )
    if verdict == "rejected":
        return SEND_REJECTED
    if verdict == "acked" or user_echo(exchange, events) is not None:
        return SENT
    return SEND_UNCERTAIN


def observe(delivery: str, association: Association) -> str:
    """Move to reply_observed only on a unique tied match.

    Rejection and failure stay where they are. Ambiguity stays ambiguous so
    a later poll cannot turn it back into a clean send waiting for "the" reply.
    ``delivery_state`` still reports whether the attempt itself was accepted.
    """
    if delivery in (FAILED_BEFORE_SEND, SEND_REJECTED):
        return delivery
    if association.state == AMBIGUOUS:
        return AMBIGUOUS
    if association.state == REPLY_OBSERVED and delivery in (SENT, SEND_UNCERTAIN):
        return REPLY_OBSERVED
    return delivery
