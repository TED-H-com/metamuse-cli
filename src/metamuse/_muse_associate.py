"""Map gateway evidence onto the SPEC §6 exchange states.

prepared → submitting → sent | send_uncertain | failed_before_send
sent / send_uncertain → reply_observed → closed

``ambiguous`` is an association result, not an exchange state. ``closed`` is
a host settlement and does not reopen. This module never resends.

An application error after bytes were handed off is attempt evidence. The
exchange stays ``send_uncertain`` until a clean acknowledgement or this
exchange's user echo. The echo promotes it to ``sent``.

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

- A candidate must follow this exchange's own user turn. Identical text with
  a different request marker is not that turn. ``req-1`` does not match
  ``req-10`` or ``req-2``.
- A late answer with no such turn, a manual user message between the turn
  and the assistant, or another user turn before the echo, is not paired.
  While the caller says an earlier exchange in the session is still
  unresolved, an assistant message after the echo is not paired either.
- When the event carries ``time_ms``, a stamp earlier than the local submit
  time is not this attempt. muse-cli does not read that field.
- Observations accumulate. The same session, event name, and seq are one
  event when a message id appears later. Ambiguity records the competing
  candidate keys. It clears only when one of those keys is disqualified on
  that event, or the host names one of them. Another event gaining
  ``reply_to_message_id``, or a competitor moving to a progress status, does
  not resolve it.
- Completion requires a status in an explicit calibrated set. The default
  set is empty. ``running`` and any other unknown value are progress.
- ``ok: false``, a non-2xx status, and a body that is not a JSON object
  (including undecodable bytes) are attempt evidence. They are not an
  exchange state and they do not block a later echo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from metamuse._muse_codec import json_depth as _json_depth
from metamuse._muse_codec import redact

PREPARED = "prepared"
SUBMITTING = "submitting"
SENT = "sent"
SEND_UNCERTAIN = "send_uncertain"
FAILED_BEFORE_SEND = "failed_before_send"
REPLY_OBSERVED = "reply_observed"
CLOSED = "closed"
AMBIGUOUS = "ambiguous"
PROGRESS = "progress"
NONE = "none"

EVIDENCE_ACKED = "acked"
EVIDENCE_APP_ERROR = "app_error"
EVIDENCE_UNDECIDED = "undecided"

# Upstream cli.py says to wait for "completed" and then accepts any truthy
# status. LIVE has not listed the enum. B09 must not treat that word as
# calibrated until a session records it. Tests pass the set explicitly when
# they need to exercise the completion gate.
UNVERIFIED_UPSTREAM_COMPLETE = frozenset({"completed"})
CALIBRATED_COMPLETE_STATUSES: frozenset[str] = frozenset()

_MARKER_KEYS = ("request_marker", "x-request-id", "client_request_id")
_OPEN_EXCHANGE = (SENT, SEND_UNCERTAIN, AMBIGUOUS)


@dataclass(frozen=True)
class Exchange:
    session_id: str
    baseline_seq: int
    sent_text: str
    request_marker: str
    submitted_at_ms: int | None = None
    # Caller fact: another exchange in this session is still unpaired.
    # Not a field muse-cli returns.
    earlier_unresolved: bool = False


@dataclass(frozen=True)
class AttemptEvidence:
    """Application result of one send attempt. Not an exchange state."""

    verdict: str
    detail: str = ""


@dataclass
class ObservationMemory:
    """Cumulative history for one exchange. A later poll must pass this back."""

    events: dict[tuple, dict] = field(default_factory=dict)
    ambiguous: bool = False
    competing: set[tuple] = field(default_factory=set)
    disqualified: set[tuple] = field(default_factory=set)

    def note(self, events: list[dict]) -> None:
        for event in events:
            ident = _identity(event)
            current = self.events.get(ident)
            if current is not None and _can_enrich(current, event):
                self._store(ident, current, event)
                continue
            if current is None:
                self.events[ident] = _copy_event(event)
                continue
            alt = ident + (_message_id(event),)
            prev = self.events.get(alt)
            if prev is None:
                self.events[alt] = _copy_event(event)
            else:
                self._store(alt, prev, event)

    def _store(self, key: tuple, prev: dict, event: dict) -> None:
        if key in self.competing and _reply_to(event) and not _reply_to(prev):
            self.disqualified.add(key)
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
    return str(event.get("reply_to_message_id") or payload.get("reply_to_message_id") or "")


def _seq(event: dict) -> int:
    value = event.get("seq", 0)
    if type(value) is int and 0 <= value <= (1 << 63) - 1:
        return value
    return -1


def _message_id(event: dict) -> str:
    payload = _payload(event)
    return str(event.get("message_id") or payload.get("message_id") or "")


def _identity(event: dict) -> tuple:
    return (
        str(event.get("session_id") or ""),
        str(event.get("event_name") or ""),
        _seq(event),
    )


def _can_enrich(prev: dict, new: dict) -> bool:
    """Same row when a message id arrives, or when the ids agree."""
    if _identity(prev) != _identity(new):
        return False
    previous_id = _message_id(prev)
    new_id = _message_id(new)
    if not previous_id or not new_id:
        return True
    return previous_id == new_id


def _event_time_ms(event: dict) -> int | None:
    payload = _payload(event)
    for source in (event, payload):
        value = source.get("time_ms")
        if type(value) is int and 0 <= value <= (1 << 63) - 1:
            return value
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
    """Any recognized marker other than this request disqualifies the event.

    A second field that matches does not cancel a conflicting field.
    """
    if not exchange.request_marker:
        return False
    return any(value != exchange.request_marker for value in _marker_values(event))


def _is_our_echo(exchange: Exchange, event: dict) -> bool:
    if event.get("session_id") != exchange.session_id:
        return False
    if event.get("event_name") != "message.user":
        return False
    if _seq(event) <= exchange.baseline_seq:
        return False
    if _before_submit(exchange, event):
        return False
    # A marker for a different request wins over identical text.
    if _marker_conflict(exchange, event):
        return False
    if _text(event) == exchange.sent_text:
        return True
    return _marker_echoed(event, exchange.request_marker)


def user_echo(exchange: Exchange, events: list[dict]) -> dict | None:
    """Earliest same-session user event after the baseline that is this send.

    Text must equal the sent text, unless a marker field names a different
    request. A matching marker field must equal the marker exactly.
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
    if _can_enrich(event, echo):
        return False
    return not _is_our_echo(exchange, event)


def _user_between(exchange: Exchange, events: list[dict], echo: dict, candidate: dict) -> bool:
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
    return Association(state, reply, ignored, tuple(progress), tuple(unresolved), memory)


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
    index = {id(event): key for key, event in memory.events.items()}
    statuses = CALIBRATED_COMPLETE_STATUSES if complete_statuses is None else complete_statuses
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

    if len(completes) > 1:
        memory.ambiguous = True
        for event in completes:
            memory.competing.add(index[id(event)])

    live = [event for event in completes if index[id(event)] not in memory.disqualified]
    picked = _host_pick(memory, index, live, host_decision)
    if picked is not None:
        memory.ambiguous = False
        return _result(REPLY_OBSERVED, picked, ignored, progress, [], memory)

    if (exchange.earlier_unresolved or _earlier_user(exchange, world, echo)) and live:
        return _result(AMBIGUOUS, None, ignored, progress, live + writing, memory)

    if memory.ambiguous:
        cleared = memory.competing & memory.disqualified
        if cleared and len(live) == 1 and index[id(live[0])] in memory.competing:
            memory.ambiguous = False
            return _result(REPLY_OBSERVED, live[0], ignored, progress, [], memory)
        retained = live if live else writing
        return _result(AMBIGUOUS, None, ignored, progress, retained, memory)

    if len(live) == 1:
        return _result(REPLY_OBSERVED, live[0], ignored, progress, [], memory)
    if len(live) > 1:
        return _result(AMBIGUOUS, None, ignored, progress, live, memory)
    if progress:
        return _result(PROGRESS, None, ignored, progress, writing, memory)
    return _result(NONE, None, ignored, [], writing, memory)


def _host_pick(
    memory: ObservationMemory,
    index: dict[int, tuple],
    live: list[dict],
    host_decision: str | None,
) -> dict | None:
    if host_decision is None:
        return None
    chosen = _match_decision(live, host_decision)
    if chosen is None:
        return None
    key = index[id(chosen)]
    if memory.competing and key not in memory.competing:
        return None
    return chosen


def attempt_evidence(
    *,
    stream_complete: bool,
    stream_status: int | None,
    body: bytes | None,
) -> AttemptEvidence:
    """Classify the application body without choosing an exchange state.

    ``app_error`` matches muse-cli ``Gateway.request`` (status outside 2xx)
    and ``call_json`` (JSON object with ``ok is False``). Those exceptions do
    not prove the send had no side effect. An empty, non-object, or
    undecodable body is ``undecided``, not an acknowledgement.
    """
    if not stream_complete or stream_status is None:
        return AttemptEvidence(EVIDENCE_UNDECIDED, "response incomplete")
    if stream_status < 200 or stream_status >= 300:
        return AttemptEvidence(EVIDENCE_APP_ERROR, f"status {stream_status}")
    if not body:
        return AttemptEvidence(EVIDENCE_UNDECIDED, "empty body")
    if len(body) > 1024 * 1024 or _json_depth(body) > 32:
        return AttemptEvidence(EVIDENCE_UNDECIDED, "body is not usable json")
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError, ValueError, OverflowError):
        return AttemptEvidence(EVIDENCE_UNDECIDED, "body is not json")
    if not isinstance(payload, dict):
        return AttemptEvidence(EVIDENCE_UNDECIDED, "body is not an object")
    if payload.get("ok") is False:
        error = payload.get("error")
        detail = f"ok false: {error}" if isinstance(error, str) else "ok false"
        return AttemptEvidence(EVIDENCE_APP_ERROR, redact(detail))
    return AttemptEvidence(EVIDENCE_ACKED, "")


def delivery_state(
    *,
    wrote_any_bytes: bool,
    stream_complete: bool,
    stream_status: int | None,
    exchange: Exchange,
    events: list[dict],
    body: bytes | None = None,
) -> str:
    """Return a SPEC §6 state. This function does not resend.

    No bytes handed off is ``failed_before_send``. A clean JSON acknowledgement
    or this exchange's user echo is ``sent``. An application error, a non-JSON
    body, or silence after the handoff is ``send_uncertain``. The error itself
    is ``attempt_evidence`` and must be stored beside the state. A later echo
    still promotes ``send_uncertain`` to ``sent``.
    """
    if not wrote_any_bytes:
        return FAILED_BEFORE_SEND
    evidence = attempt_evidence(
        stream_complete=stream_complete,
        stream_status=stream_status,
        body=body,
    )
    if evidence.verdict == EVIDENCE_ACKED or user_echo(exchange, events) is not None:
        return SENT
    return SEND_UNCERTAIN


def observe(exchange_state: str, association: Association) -> str:
    """Move ``sent`` or ``send_uncertain`` to ``reply_observed`` on one tied reply.

    ``ambiguous`` is not produced from a SPEC state. If the caller stored that
    association label in the exchange slot, a resolved association moves it to
    ``reply_observed``. ``closed``, ``reply_observed``, and
    ``failed_before_send`` do not reopen. Nothing here resends.
    """
    if exchange_state in (CLOSED, REPLY_OBSERVED, FAILED_BEFORE_SEND):
        return exchange_state
    if association.state == REPLY_OBSERVED and exchange_state in _OPEN_EXCHANGE:
        return REPLY_OBSERVED
    if exchange_state == AMBIGUOUS and association.state == AMBIGUOUS:
        return AMBIGUOUS
    return exchange_state
