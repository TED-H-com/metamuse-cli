"""Deterministic, explicitly selected in-process Muse fixture. Never opens sockets."""

import json
from dataclasses import dataclass, field
from typing import Any

from metamuse import models as m
from metamuse.muse import (
    AttemptEvidence,
    GatewayResult,
    MuseStateUnknown,
    StreamAttempt,
)


@dataclass
class OfflineMuseGateway:
    store: Any = field(default=None, repr=False)
    mode: str = "normal"
    pages: list[list[dict]] = field(default_factory=list, repr=False)
    calls: list[tuple[str, str]] = field(default_factory=list, repr=False)
    sessions: dict[str, dict] = field(default_factory=dict, repr=False)
    events: dict[str, list[dict]] = field(default_factory=dict, repr=False)
    backend: str = field(default="fixture", init=False)

    def close(self):
        pass

    def _result(self, route, body, marker="read", digest=None):
        return GatewayResult(
            route,
            200,
            m.canonical_json(body),
            len(self.calls),
            marker,
            digest or m.payload_digest({}),
            True,
        )

    def session_start(self, prepared, *, authorization):
        from hashlib import sha256

        from metamuse.muse import _thaw
        from metamuse.safety import action_digest

        body = json.loads(prepared.body)
        if (
            body.get("method") != "/api/session/start"
            or body.get("params", {}).get("origin") != "fresh"
            or body.get("params", {}).get("lifecycle") != "persistent"
            or not isinstance(body.get("params", {}).get("title"), str)
        ):
            raise m.CoreError(m.ErrorCode.PROTOCOL_MISMATCH, "Fixture refused invalid session wire")
        authorization.consume(
            action_digest=action_digest(_thaw(prepared.target), _thaw(prepared.payload))
        )
        self.calls.append(("session.start", prepared.marker))
        session = "fixture-session-" + prepared.marker
        self.sessions[session] = {"session_id": session, "request_marker": prepared.marker}
        if self.mode == "open_timeout":
            raise MuseStateUnknown(
                request_marker=prepared.marker,
                outbound_digest=sha256(prepared.body).hexdigest(),
                stream_id=len(self.calls),
                bytes_handed_off=True,
            )
        return self._result(
            "session.start",
            {"ok": True, "result": {"session_id": session}},
            prepared.marker,
            sha256(prepared.body).hexdigest(),
        )

    def chat_stream(self, prepared, *, authorization, **kwargs):
        from hashlib import sha256

        # Use the same digest implementation as production; thaw nested mappings via JSON.
        from metamuse.muse import _thaw
        from metamuse.safety import action_digest

        authorization.consume(
            action_digest=action_digest(_thaw(prepared.target), _thaw(prepared.payload))
        )
        self.calls.append(("chat.stream", prepared.marker))
        digest = sha256(prepared.body).hexdigest()
        if self.mode == "not_submitted":
            return StreamAttempt(
                digest,
                prepared.marker,
                None,
                False,
                AttemptEvidence("undecided", "before handoff"),
                "failed_before_send",
                error_code="E_NETWORK_TIMEOUT",
            )
        body = json.loads(prepared.body)
        session = body["session_id"]
        baseline = kwargs["exchange"].baseline_seq
        self.events.setdefault(session, []).extend(
            [
                {
                    "session_id": session,
                    "event_name": "message.user",
                    "seq": baseline + 1,
                    "message_id": prepared.marker + "-user",
                    "request_marker": prepared.marker,
                    "payload": {"role": "user", "content": body["items"][0]["text"]},
                },
                {
                    "session_id": session,
                    "event_name": "message.assistant",
                    "seq": baseline + 2,
                    "message_id": prepared.marker + "-reply",
                    "payload": {
                        "role": "assistant",
                        "status": "running",
                        "content": "Fixture reply, part one.",
                    },
                },
            ]
        )
        uncertain = self.mode in ("send_timeout", "app_error")
        return StreamAttempt(
            digest,
            prepared.marker,
            len(self.calls),
            True,
            AttemptEvidence(
                "app_error" if self.mode == "app_error" else "undecided" if uncertain else "acked"
            ),
            "send_uncertain" if uncertain else "sent",
            status=None if uncertain else 200,
            body=b"" if uncertain else b'{"ok":true}',
            error_code="E_EXTERNAL_STATE_UNKNOWN" if uncertain else None,
        )

    def sessions_list(self, **kwargs):
        self.calls.append(("sessions.list", "read"))
        if self.store is not None:
            with self.store.connection() as conn:
                for row in conn.execute(
                    "SELECT open_payload FROM discussions WHERE backend='fixture' "
                    "AND open_bytes_handed_off=1"
                ):
                    marker = json.loads(row[0])["marker"]
                    session = "fixture-session-" + marker
                    self.sessions.setdefault(
                        session, {"session_id": session, "request_marker": marker}
                    )
        return self._result(
            "sessions.list", {"ok": True, "result": {"sessions": list(self.sessions.values())}}
        )

    def chat_history(self, *, session_id, **kwargs):
        self.calls.append(("chat.history", session_id))
        events = self.pages.pop(0) if self.pages else self.events.get(session_id, [])
        if not events and self.store is not None:
            # Normal fixture can reconstruct its deterministic world after a CLI restart.
            with self.store.connection() as conn:
                rows = conn.execute(
                    (
                        "SELECT * FROM exchanges WHERE discussion_id IN (SELECT id FROM discuss"
                        "ions WHERE remote_session=?) AND bytes_handed_off=1 ORDER BY rowid"
                    ),
                    (session_id,),
                ).fetchall()
                for row in rows:
                    exchange = self.store.decode(m.Exchange, row)
                    body = exchange.outbound_payload.body
                    seq = exchange.baseline.get("seq", 0)
                    events = [
                        *events,
                        {
                            "session_id": session_id,
                            "event_name": "message.user",
                            "seq": seq + 1,
                            "request_marker": exchange.request_marker,
                            "message_id": exchange.request_marker + "-user",
                            "payload": {"role": "user", "content": body["items"][0]["text"]},
                        },
                        {
                            "session_id": session_id,
                            "event_name": "message.assistant",
                            "seq": seq + 2,
                            "message_id": exchange.request_marker + "-reply",
                            "payload": {
                                "role": "assistant",
                                "status": "running",
                                "content": "Fixture reply, part one.",
                            },
                        },
                    ]
        return self._result("chat.history", {"ok": True, "result": {"chat_events": events}})
