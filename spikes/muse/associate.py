"""Map gateway evidence onto the SPEC §6 exchange states.

prepared → submitting → sent | send_uncertain | failed_before_send
sent / send_uncertain → reply_observed → closed

`closed` is a host settlement (answered / needs_more_data / abandoned). No
gateway frame produces it. This module never resends.
"""

from __future__ import annotations

from dataclasses import dataclass

PREPARED = "prepared"
SUBMITTING = "submitting"
SENT = "sent"
SEND_UNCERTAIN = "send_uncertain"
FAILED_BEFORE_SEND = "failed_before_send"
REPLY_OBSERVED = "reply_observed"
CLOSED = "closed"
AMBIGUOUS = "ambiguous"
NONE = "none"


@dataclass(frozen=True)
class Exchange:
    session_id: str
    baseline_seq: int
    sent_text: str
    request_marker: str


@dataclass(frozen=True)
class Association:
    state: str
    reply: dict | None
    ignored_other_session: int


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


def _text(event: dict) -> str:
    payload = _payload(event)
    return str(payload.get("display_text") or payload.get("content") or "")


def _reply_to(event: dict) -> str:
    payload = _payload(event)
    return str(event.get("reply_to_message_id") or payload.get("reply_to_message_id") or "")


def _marker_echoed(event: dict, marker: str) -> bool:
    if not marker:
        return False
    payload = _payload(event)
    blob = " ".join(
        str(part)
        for part in (
            event.get("request_marker"),
            event.get("x-request-id"),
            payload.get("request_marker"),
            payload.get("client_request_id"),
        )
        if part
    )
    return marker in blob


def user_echo(exchange: Exchange, events: list[dict]) -> dict | None:
    """A same-session user event after the baseline, matched by text or marker.

    Text match is the evidence muse-cli's history shape can support today.
    A server echo of x-request-id was not found in the client. Marker match
    stays in the predicate so a LIVE confirmation can use it without a rewrite.
    """
    for event in events:
        if event.get("session_id") != exchange.session_id:
            continue
        if event.get("event_name") != "message.user":
            continue
        if int(event.get("seq") or 0) <= exchange.baseline_seq:
            continue
        if _text(event) == exchange.sent_text or _marker_echoed(event, exchange.request_marker):
            return event
    return None


def _reply_candidate(exchange: Exchange, event: dict) -> bool:
    if event.get("session_id") != exchange.session_id:
        return False
    if event.get("event_name") != "message.assistant":
        return False
    role = _payload(event).get("role")
    if role not in (None, "", "assistant"):
        return False
    if int(event.get("seq") or 0) <= exchange.baseline_seq:
        return False
    if _reply_to(event):
        return False
    if not _text(event):
        return False
    # muse-cli waits until payload.status is non-empty before showing the text.
    # A non-empty status is not host settlement and is not `closed`.
    return bool(_payload(event).get("status"))


def associate_reply(exchange: Exchange, events: list[dict]) -> Association:
    """Pair at most one assistant reply. Another session is ignored, not "latest"."""
    ignored = 0
    candidates = []
    for event in events:
        if event.get("session_id") != exchange.session_id:
            if event.get("event_name") == "message.assistant":
                ignored += 1
            continue
        if _reply_candidate(exchange, event):
            candidates.append(event)
    candidates.sort(key=lambda event: int(event.get("seq") or 0))
    if len(candidates) == 1:
        return Association(REPLY_OBSERVED, candidates[0], ignored)
    if len(candidates) > 1:
        return Association(AMBIGUOUS, None, ignored)
    return Association(NONE, None, ignored)


def delivery_state(
    *,
    wrote_any_bytes: bool,
    stream_complete: bool,
    stream_status: int | None,
    exchange: Exchange,
    events: list[dict],
) -> str:
    """Decide sent / uncertain / failed after a single attempt.

    A finished 2xx application response (end_body already required by the
    caller via stream_complete) is the send acknowledgement. A same-session
    user echo is the other positive evidence. Anything else, once bytes were
    handed to the transport, stays send_uncertain. Silence is not failure.
    """
    if not wrote_any_bytes:
        return FAILED_BEFORE_SEND
    acked = bool(stream_complete and stream_status is not None and 200 <= stream_status < 300)
    if acked or user_echo(exchange, events) is not None:
        return SENT
    return SEND_UNCERTAIN


def observe(delivery: str, association: Association) -> str:
    """Move sent or send_uncertain to reply_observed only on a unique match."""
    if delivery == FAILED_BEFORE_SEND:
        return FAILED_BEFORE_SEND
    if association.state == REPLY_OBSERVED and delivery in (SENT, SEND_UNCERTAIN):
        return REPLY_OBSERVED
    return delivery
