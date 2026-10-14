"""Muse transport and the feature seam.

B09 owns the gateway transport: HTTPS auth, one Noise_XX WebSocket, serial
unary calls on the four allowlisted routes, and the attempt evidence B10
persists. Discussion lifecycle stays unimplemented until B10.

Nothing in this module is sent unless the caller passes ``network_mode="live"``,
a credential provider, and an outbound guard. Mutations also require
``external_submit=True``, which B10 may set only after
``step.before_external_action`` returns. This module cannot see that helper
without changing the executor, so the flag is the precondition. See
``docs/dev/MUSE.md``.

``curl_cffi`` and ``noiseprotocol`` are imported by the adapter and the Noise
party on first use, not at import time.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.parse
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from metamuse import models as m
from metamuse._muse_associate import (
    AMBIGUOUS,
    CALIBRATED_COMPLETE_STATUSES,
    CLOSED,
    EVIDENCE_ACKED,
    EVIDENCE_APP_ERROR,
    EVIDENCE_UNDECIDED,
    FAILED_BEFORE_SEND,
    NONE,
    PREPARED,
    PROGRESS,
    REPLY_OBSERVED,
    SEND_UNCERTAIN,
    SENT,
    SUBMITTING,
    UNVERIFIED_UPSTREAM_COMPLETE,
    Association,
    AttemptEvidence,
    Exchange,
    ObservationMemory,
    associate_reply,
    attempt_evidence,
    delivery_state,
    normalize_event,
    observe,
    user_echo,
)
from metamuse._muse_codec import Header, ProtocolMismatch, redact
from metamuse._muse_http import (
    AddressPin,
    Connector,
    CurlConnector,
    CurlHttp,
    HttpClient,
    HttpResponse,
)
from metamuse._muse_link import (
    DEFAULT_COLLECT_TIMEOUT_S,
    IncompleteBody,
    OwnedLink,
    ReceiveLoop,
    SendUncertain,
)
from metamuse._muse_noise import NoiseParty, handshake_initiator
from metamuse.models import CoreError, ErrorCode

if TYPE_CHECKING:
    from metamuse.app import AppContext
    from metamuse.jobs import Operation

__all__ = [
    "AMBIGUOUS",
    "CALIBRATED_COMPLETE_STATUSES",
    "CLOSED",
    "EVIDENCE_ACKED",
    "EVIDENCE_APP_ERROR",
    "EVIDENCE_UNDECIDED",
    "FAILED_BEFORE_SEND",
    "HATCH_WSS_BASE",
    "MUSE_ORIGIN",
    "NONE",
    "PREPARED",
    "PROGRESS",
    "REPLY_OBSERVED",
    "SEND_UNCERTAIN",
    "SENT",
    "SUBMITTING",
    "UNVERIFIED_UPSTREAM_COMPLETE",
    "VM_ADDRESS_TEMPLATE",
    "AddressPin",
    "Association",
    "AttemptEvidence",
    "Exchange",
    "GatewayResult",
    "MuseProgrammingError",
    "MuseTransport",
    "ObservationMemory",
    "StreamAttempt",
    "associate_reply",
    "attempt_evidence",
    "close_muse_exchange",
    "delivery_state",
    "normalize_event",
    "observe",
    "open_muse_discussion",
    "read_muse_exchange",
    "redact",
    "register_operations",
    "submit_muse_exchange",
    "user_echo",
]

# Operator-facing hosts. The VM template is a hatch-token JSON field only.
MUSE_ORIGIN = "https://muse.ai"
HATCH_WSS_BASE = "wss://hatch.metaaivm.com/v1/noise"
VM_ADDRESS_TEMPLATE = "wss://{vm_id}.metaaivm.com/"

_VM_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_TOKEN = re.compile(r"^[A-Za-z0-9._~+/=-]{1,4096}$")
_MARKER = re.compile(r"^[A-Za-z0-9._~-]{1,128}$")
_HTTP_TIMEOUT_S = 20.0
_MUTATING = frozenset({"session.start", "chat.stream"})


class MuseProgrammingError(RuntimeError):
    """A route or argument the transport refuses before any I/O."""


class CredentialProvider(Protocol):
    """B08 supplies the operator cookie jar for an opaque ``auth_ref``."""

    def cookies(self, auth_ref: str) -> str: ...


class OutboundGuard(Protocol):
    """B08 URL policy. Called before every HTTPS and WSS request.

    Return ``None`` to allow the URL as written, or an :class:`AddressPin`
    the adapter must apply. Raise :class:`CoreError` to deny.
    """

    def allow(self, url: str, *, purpose: str) -> AddressPin | None: ...


@dataclass(frozen=True)
class _Route:
    verb: str
    path: str
    mutating: bool


ROUTES: dict[str, _Route] = {
    "session.start": _Route("POST", "/session/start", True),
    "sessions.list": _Route("GET", "/api/session/list", False),
    "chat.stream": _Route("POST", "/chat/stream", True),
    "chat.history": _Route("GET", "/chat/history", False),
}


@dataclass(frozen=True)
class GatewayResult:
    """A finished unary read or a finished ``session.start``."""

    route: str
    status: int
    body: bytes
    stream_id: int
    request_marker: str
    outbound_digest: str
    bytes_handed_off: bool

    def __repr__(self) -> str:
        return (
            f"GatewayResult(route={self.route!r}, status={self.status!r}, "
            f"stream_id={self.stream_id!r}, bytes_handed_off={self.bytes_handed_off!r})"
        )


@dataclass(frozen=True)
class StreamAttempt:
    """What B10 persists for one ``chat.stream`` attempt. No credentials."""

    outbound_digest: str
    request_marker: str
    stream_id: int | None
    bytes_handed_off: bool
    evidence: AttemptEvidence
    state: str
    status: int | None = None
    body: bytes = b""
    error_code: str | None = None

    def __repr__(self) -> str:
        return (
            f"StreamAttempt(state={self.state!r}, verdict={self.evidence.verdict!r}, "
            f"bytes_handed_off={self.bytes_handed_off!r}, stream_id={self.stream_id!r}, "
            f"error_code={self.error_code!r}, detail={redact(self.evidence.detail)!r})"
        )


def _error(
    code: ErrorCode,
    message: str,
    *,
    next_action: str | None = None,
    retry_after: float = 0,
) -> CoreError:
    return CoreError(code, redact(message), next_action=next_action, retry_after=retry_after)


def _public_body(body: bytes) -> bytes:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return body
    return redact(text).encode("utf-8")


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _retry_after(headers: Mapping[str, str]) -> float:
    for key, value in headers.items():
        if key.lower() == "retry-after" and value.isdigit():
            return float(int(value))
    return 0


def _json_object(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise _error(
            ErrorCode.PROTOCOL_MISMATCH,
            "Muse HTTPS response was not JSON",
            next_action="Stop. Do not retry a damaged auth response.",
        ) from None
    if not isinstance(value, dict):
        raise _error(
            ErrorCode.PROTOCOL_MISMATCH,
            "Muse HTTPS response was not an object",
            next_action="Stop. Do not retry a damaged auth response.",
        )
    return value


def _wss_url(vm_id: str, token: str) -> str:
    quoted_vm = urllib.parse.quote(vm_id, safe="")
    quoted = urllib.parse.quote(token, safe="")
    return f"{HATCH_WSS_BASE}?vm_id={quoted_vm}&auth_token={quoted}"


def _request_bytes(payload: dict | None) -> bytes:
    if payload is None:
        return b""
    try:
        return m.canonical_json(payload)
    except ValueError:
        raise MuseProgrammingError("request body is not a JSON object") from None


def _apply_query(path: str, query: Mapping[str, Any]) -> str:
    clean = {
        key: value for key, value in query.items() if value is not None and key != "transcript_mode"
    }
    if not clean:
        return path
    return path + "?" + urllib.parse.urlencode(clean)


def _exchange_for(body: dict | None, marker: str, exchange: Exchange | None) -> Exchange:
    if exchange is not None:
        return exchange
    text = ""
    if isinstance(body, dict):
        items = body.get("items")
        if isinstance(items, list) and items and isinstance(items[0], dict):
            raw_text = items[0].get("text")
            if isinstance(raw_text, str):
                text = raw_text
        elif isinstance(body.get("text"), str):
            text = body["text"]
    return Exchange(session_id="", baseline_seq=0, sent_text=text, request_marker=marker)


class MuseTransport:
    """One gateway connection. One receive loop. Serial unary calls.

    Defaults refuse. ``network_mode="offline"`` refuses with ``E_SCOPE_DENIED``.
    A missing credential provider refuses with ``E_AUTH_REQUIRED``. A missing
    outbound guard refuses with ``E_SCOPE_REQUIRED``. ``session.start`` and
    ``chat.stream`` also refuse until ``external_submit`` is true.
    """

    def __init__(
        self,
        *,
        auth_ref: str = "",
        credentials: CredentialProvider | None = None,
        guard: OutboundGuard | None = None,
        clock: Callable[[], float] | None = None,
        network_mode: str = "offline",
        http: HttpClient | None = None,
        connector: Connector | None = None,
        proxy: str | None = None,
    ) -> None:
        self._auth_ref = auth_ref
        self._credentials = credentials
        self._guard = guard
        self._clock = clock or time.monotonic
        self._network_mode = network_mode
        self._http = http if http is not None else CurlHttp()
        self._connector = connector if connector is not None else CurlConnector()
        self._proxy = proxy
        self._lock = threading.Lock()
        self._loop: ReceiveLoop | None = None

    def __repr__(self) -> str:
        return (
            f"MuseTransport(network_mode={self._network_mode!r}, "
            f"connected={self._loop is not None})"
        )

    def __enter__(self) -> MuseTransport:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def close(self) -> None:
        loop = self._loop
        self._loop = None
        if loop is None:
            return
        loop.__exit__(None, None, None)
        loop.link.close()

    def session_start(
        self,
        body: dict[str, Any],
        *,
        external_submit: bool = False,
        timeout_s: float = DEFAULT_COLLECT_TIMEOUT_S,
    ) -> GatewayResult:
        result = self.call(
            "session.start",
            body=body,
            external_submit=external_submit,
            timeout_s=timeout_s,
        )
        if not isinstance(result, GatewayResult):
            raise MuseProgrammingError("session.start did not return a gateway result")
        return result

    def sessions_list(self, *, timeout_s: float = DEFAULT_COLLECT_TIMEOUT_S) -> GatewayResult:
        result = self.call("sessions.list", timeout_s=timeout_s)
        if not isinstance(result, GatewayResult):
            raise MuseProgrammingError("sessions.list did not return a gateway result")
        return result

    def chat_history(
        self,
        *,
        session_id: str | None = None,
        limit: int | None = None,
        timeout_s: float = DEFAULT_COLLECT_TIMEOUT_S,
    ) -> GatewayResult:
        query: dict[str, Any] = {}
        if session_id is not None:
            query["session_id"] = session_id
        if limit is not None:
            query["limit"] = limit
        result = self.call("chat.history", query=query, timeout_s=timeout_s)
        if not isinstance(result, GatewayResult):
            raise MuseProgrammingError("chat.history did not return a gateway result")
        return result

    def chat_stream(
        self,
        body: dict[str, Any],
        *,
        external_submit: bool = False,
        timeout_s: float = DEFAULT_COLLECT_TIMEOUT_S,
        exchange: Exchange | None = None,
        events: list[dict[str, Any]] | None = None,
    ) -> StreamAttempt:
        result = self.call(
            "chat.stream",
            body=body,
            external_submit=external_submit,
            timeout_s=timeout_s,
            exchange=exchange,
            events=events,
        )
        if not isinstance(result, StreamAttempt):
            raise MuseProgrammingError("chat.stream did not return an attempt")
        return result

    def call(
        self,
        route: str,
        *,
        body: dict[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
        external_submit: bool = False,
        timeout_s: float = DEFAULT_COLLECT_TIMEOUT_S,
        exchange: Exchange | None = None,
        events: list[dict[str, Any]] | None = None,
    ) -> GatewayResult | StreamAttempt:
        spec = ROUTES.get(route)
        if spec is None:
            raise MuseProgrammingError(f"route {route!r} is not on the Muse allowlist")
        self._ready(spec, external_submit=external_submit)
        marker = self._marker(exchange)
        self._validate_body(spec, body, query)
        if not self._lock.acquire(blocking=False):
            raise ProtocolMismatch("concurrent collection")
        try:
            self._connect()
            return self._unary(
                route,
                spec,
                body=body,
                query=query,
                timeout_s=timeout_s,
                exchange=exchange,
                events=events,
                marker=marker,
            )
        finally:
            self._lock.release()

    def _ready(self, spec: _Route, *, external_submit: bool) -> None:
        if self._network_mode == "offline":
            raise _error(
                ErrorCode.SCOPE_DENIED,
                "External operations are disabled in offline mode",
                next_action="Enable live mode explicitly and configure the required permission",
            )
        if self._network_mode != "live":
            raise MuseProgrammingError("network_mode must be offline or live")
        if self._credentials is None:
            raise _error(
                ErrorCode.AUTH_REQUIRED,
                "No credential provider is configured",
                next_action="Inject a credential provider that returns the cookie jar for auth_ref",
            )
        if not self._auth_ref:
            raise _error(
                ErrorCode.AUTH_REQUIRED,
                "auth_ref is required",
                next_action="Pass the opaque auth_ref from the discussion row",
            )
        if self._guard is None:
            raise _error(
                ErrorCode.SCOPE_REQUIRED,
                "No outbound URL guard is configured",
                next_action="Inject an outbound URL guard before any Muse request",
            )
        if spec.mutating and not external_submit:
            raise _error(
                ErrorCode.SCOPE_REQUIRED,
                "Muse mutations require the external-action boundary",
                next_action=(
                    "Call before_external_action and pass external_submit=True "
                    "only after it returns"
                ),
            )

    def _cookie_jar(self) -> str:
        provider = self._credentials
        if provider is None:
            raise _error(
                ErrorCode.AUTH_REQUIRED,
                "No credential provider is configured",
                next_action="Inject a credential provider that returns the cookie jar for auth_ref",
            )
        jar = provider.cookies(self._auth_ref)
        if not isinstance(jar, str) or not jar or "\r" in jar or "\n" in jar:
            raise _error(
                ErrorCode.AUTH_REQUIRED,
                "Credential jar is not usable",
                next_action="Return one cookie header value with no line breaks",
            )
        return jar

    def _https(
        self,
        method: str,
        path: str,
        *,
        cookies: str,
        body: bytes | None,
        purpose: str,
        bearer: str | None = None,
    ) -> dict[str, Any]:
        guard = self._guard
        if guard is None:
            raise _error(
                ErrorCode.SCOPE_REQUIRED,
                "No outbound URL guard is configured",
                next_action="Inject an outbound URL guard before any Muse request",
            )
        url = MUSE_ORIGIN + path
        pin = guard.allow(url, purpose=purpose)
        headers = {
            "Origin": MUSE_ORIGIN,
            "Referer": MUSE_ORIGIN + "/",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Cookie": cookies,
        }
        if bearer is not None:
            headers["Authorization"] = f"Bearer {bearer}"
        response = self._http.request(
            method,
            url,
            headers=headers,
            body=body,
            pins=pin,
            timeout=_HTTP_TIMEOUT_S,
            proxy=self._proxy,
        )
        return self._read_https(path, response)

    def _read_https(self, path: str, response: HttpResponse) -> dict[str, Any]:
        status = response.status
        if 300 <= status < 400:
            raise _error(
                ErrorCode.SCOPE_REQUIRED,
                "The Muse host redirected the request",
                next_action=(
                    "Allow the redirect target through the outbound guard. "
                    "The transport does not follow redirects."
                ),
            )
        if status == 429:
            raise _error(
                ErrorCode.RATE_LIMITED,
                "Muse rate limit",
                next_action="Wait and retry this read. Do not resend a handed-off chat.",
                retry_after=_retry_after(response.headers),
            )
        if status in (401, 403):
            raise _error(
                ErrorCode.AUTH_REQUIRED,
                "Muse authentication failed",
                next_action="Refresh the operator cookie jar for this auth_ref",
            )
        if status == 503 or status >= 500:
            raise _error(
                ErrorCode.RESOURCE_UNAVAILABLE,
                "The Muse HTTPS endpoint is unavailable",
                next_action="Retry later. This is not an authentication failure.",
            )
        if status != 200:
            raise _error(
                ErrorCode.AUTH_REQUIRED,
                "Muse authentication failed",
                next_action="Refresh the operator cookie jar for this auth_ref",
            )
        payload = _json_object(response.body)
        if path == "/api/session":
            return self._session_payload(payload)
        if path == "/api/auth/check":
            token = payload.get("access_token")
            if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
                raise _error(
                    ErrorCode.AUTH_REQUIRED,
                    "Muse authentication response did not include a usable token",
                    next_action="Refresh the operator cookie jar for this auth_ref",
                )
            return payload
        if path == "/api/hatch/token":
            token = payload.get("token")
            if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
                raise _error(
                    ErrorCode.AUTH_REQUIRED,
                    "Muse hatch response did not include a usable token",
                    next_action="Refresh the operator cookie jar for this auth_ref",
                )
            return payload
        raise MuseProgrammingError("unexpected Muse HTTPS path")

    def _session_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "vm_id" not in payload:
            raise _error(
                ErrorCode.RESOURCE_UNAVAILABLE,
                "The Muse VM is unavailable",
                next_action="Retry later. Do not treat this as an authentication failure.",
            )
        vm_id = payload.get("vm_id")
        if not isinstance(vm_id, str) or _VM_ID.fullmatch(vm_id) is None:
            raise _error(
                ErrorCode.PROTOCOL_MISMATCH,
                "Muse session vm_id was not usable",
                next_action="Stop. Do not dial a host derived from vm_id.",
            )
        return payload

    def _connect(self) -> None:
        if self._loop is not None:
            return
        jar = self._cookie_jar()
        auth = self._https("POST", "/api/auth/check", cookies=jar, body=b"", purpose="auth.check")
        access = auth["access_token"]
        session = self._https("GET", "/api/session", cookies=jar, body=None, purpose="session")
        vm_id = session["vm_id"]
        hatch_body = m.canonical_json(
            {"vmAddress": VM_ADDRESS_TEMPLATE.format(vm_id=vm_id), "vmName": vm_id}
        )
        hatch = self._https(
            "POST",
            "/api/hatch/token",
            cookies=jar,
            body=hatch_body,
            purpose="hatch.token",
            bearer=access,
        )
        url = _wss_url(vm_id, hatch["token"])
        guard = self._guard
        if guard is None:
            raise _error(
                ErrorCode.SCOPE_REQUIRED,
                "No outbound URL guard is configured",
                next_action="Inject an outbound URL guard before any Muse request",
            )
        pin = guard.allow(url, purpose="wss.noise")
        raw = None
        loop: ReceiveLoop | None = None
        try:
            raw = self._connector.connect(url, pins=pin, timeout=_HTTP_TIMEOUT_S, proxy=self._proxy)
            party = NoiseParty("initiator")
            owned = OwnedLink(raw)
            loop = ReceiveLoop(owned, noise=party, clock=self._clock)
            loop.__enter__()
            handshake_initiator(
                party,
                owned,
                timeout=DEFAULT_COLLECT_TIMEOUT_S,
                clock=self._clock,
            )
        except IncompleteBody:
            self._drop(loop, raw)
            raise _error(
                ErrorCode.NETWORK_TIMEOUT,
                "Muse handshake timed out",
                next_action="Retry the connection. No gateway request frame was sent.",
            ) from None
        except ProtocolMismatch:
            self._drop(loop, raw)
            raise
        except CoreError:
            self._drop(loop, raw)
            raise
        except Exception as exc:
            self._drop(loop, raw)
            raise _error(
                ErrorCode.NETWORK_TIMEOUT,
                redact(str(exc)),
                next_action="Retry the connection. No gateway request frame was sent.",
            ) from None
        else:
            self._loop = loop

    def _drop(self, loop: ReceiveLoop | None, raw: Any) -> None:
        if loop is not None:
            loop.__exit__(None, None, None)
            loop.link.close()
            return
        if raw is not None:
            raw.close()

    def _unary(
        self,
        route: str,
        spec: _Route,
        *,
        body: dict[str, Any] | None,
        query: Mapping[str, Any] | None,
        timeout_s: float,
        exchange: Exchange | None,
        events: list[dict[str, Any]] | None,
        marker: str,
    ) -> GatewayResult | StreamAttempt:
        payload = body
        query_items = dict(query or {})
        if spec.verb == "GET" and payload is not None and not query_items:
            query_items = dict(payload)
            payload = None
        path = _apply_query(spec.path, query_items)
        raw = _request_bytes(payload)
        headers = [Header("x-request-id", marker)]
        if raw:
            headers.append(Header("content-type", "application/json"))
        loop = self._loop
        if loop is None:
            raise MuseProgrammingError("gateway connection is not open")
        stream_id: int | None = None
        try:
            stream_id = loop.open_request(verb=spec.verb, path=path, body=raw, headers=headers)
            status, response_body = loop.collect(stream_id, timeout_s=timeout_s)
        except IncompleteBody as exc:
            return self._incomplete(route, exc, loop, marker, raw, body, exchange, events)
        except ProtocolMismatch as exc:
            raise self._protocol(route, exc, loop) from None
        except SendUncertain as exc:
            return self._after_uncertain_send(route, exc, loop, marker, raw, body, exchange, events)
        except CoreError:
            raise
        except Exception as exc:
            if route == "chat.stream" and not loop.handed_off:
                return self._never_sent(marker, raw, redact(str(exc)))
            raise _error(
                ErrorCode.NETWORK_TIMEOUT,
                redact(str(exc)),
                next_action="Retry the read if no frame was handed off.",
            ) from None
        public = _public_body(response_body)
        if route == "chat.stream":
            return self._stream_attempt(
                status=status,
                response_body=public,
                stream_id=stream_id,
                marker=marker,
                raw=raw,
                body=body,
                exchange=exchange,
                events=events,
                handed=True,
                error_code=None,
            )
        return GatewayResult(
            route=route,
            status=status,
            body=public,
            stream_id=stream_id,
            request_marker=marker,
            outbound_digest=_digest(raw),
            bytes_handed_off=True,
        )

    def _validate_body(
        self,
        spec: _Route,
        body: dict[str, Any] | None,
        query: Mapping[str, Any] | None,
    ) -> None:
        payload = body
        if spec.verb == "GET" and payload is not None and not query:
            payload = None
        _request_bytes(payload)

    def _marker(self, exchange: Exchange | None) -> str:
        if exchange is not None and exchange.request_marker:
            marker = exchange.request_marker
        else:
            marker = str(uuid.uuid4())
        if _MARKER.fullmatch(marker) is None:
            raise MuseProgrammingError("request marker is not usable")
        return marker

    def _incomplete(
        self,
        route: str,
        exc: IncompleteBody,
        loop: ReceiveLoop,
        marker: str,
        raw: bytes,
        body: dict[str, Any] | None,
        exchange: Exchange | None,
        events: list[dict[str, Any]] | None,
    ) -> GatewayResult | StreamAttempt:
        handed = loop.handed_off
        if route == "session.start" and handed:
            raise _error(
                ErrorCode.EXTERNAL_STATE_UNKNOWN,
                "session.start timed out after the request was handed off",
                next_action=(
                    "Reconcile with sessions.list by session_id. Do not start another session."
                ),
            ) from None
        if route == "chat.stream":
            if not handed:
                return self._never_sent(marker, raw, redact(exc.detail.message))
            return self._stream_attempt(
                status=None,
                response_body=b"",
                stream_id=loop.last_stream_id,
                marker=marker,
                raw=raw,
                body=body,
                exchange=exchange,
                events=events,
                handed=True,
                error_code=str(exc.detail.code),
                stream_complete=False,
            )
        raise exc

    def _protocol(self, route: str, exc: ProtocolMismatch, loop: ReceiveLoop) -> CoreError:
        if loop.handed_off and route in _MUTATING:
            return _error(
                ErrorCode.PROTOCOL_MISMATCH,
                exc.detail.message,
                next_action="Stop. Delivery is unknown. Do not resend and do not weaken TLS.",
            )
        return exc

    def _after_uncertain_send(
        self,
        route: str,
        exc: SendUncertain,
        loop: ReceiveLoop,
        marker: str,
        raw: bytes,
        body: dict[str, Any] | None,
        exchange: Exchange | None,
        events: list[dict[str, Any]] | None,
    ) -> StreamAttempt:
        if route == "session.start":
            raise _error(
                ErrorCode.EXTERNAL_STATE_UNKNOWN,
                "session.start may have been handed off",
                next_action=(
                    "Reconcile with sessions.list by session_id. Do not start another session."
                ),
            ) from None
        if route != "chat.stream":
            raise _error(
                ErrorCode.EXTERNAL_STATE_UNKNOWN,
                "A gateway read may have been handed off",
                next_action="Reconcile with a later read. Do not repeat a mutation.",
            ) from None
        detail = redact(str(exc))
        return self._stream_attempt(
            status=None,
            response_body=b"",
            stream_id=loop.last_stream_id,
            marker=marker,
            raw=raw,
            body=body,
            exchange=exchange,
            events=events,
            handed=True,
            error_code=ErrorCode.EXTERNAL_STATE_UNKNOWN.value,
            stream_complete=False,
            detail=detail,
        )

    def _never_sent(self, marker: str, raw: bytes, detail: str) -> StreamAttempt:
        evidence = AttemptEvidence(EVIDENCE_UNDECIDED, redact(detail))
        return StreamAttempt(
            outbound_digest=_digest(raw),
            request_marker=marker,
            stream_id=None,
            bytes_handed_off=False,
            evidence=evidence,
            state=FAILED_BEFORE_SEND,
            error_code=ErrorCode.NETWORK_TIMEOUT.value,
        )

    def _stream_attempt(
        self,
        *,
        status: int | None,
        response_body: bytes,
        stream_id: int | None,
        marker: str,
        raw: bytes,
        body: dict[str, Any] | None,
        exchange: Exchange | None,
        events: list[dict[str, Any]] | None,
        handed: bool,
        error_code: str | None,
        stream_complete: bool = True,
        detail: str | None = None,
    ) -> StreamAttempt:
        if detail is None:
            evidence = attempt_evidence(
                stream_complete=stream_complete,
                stream_status=status,
                body=response_body,
            )
        else:
            evidence = AttemptEvidence(EVIDENCE_UNDECIDED, redact(detail))
        row = _exchange_for(body, marker, exchange)
        state = delivery_state(
            wrote_any_bytes=handed,
            stream_complete=stream_complete,
            stream_status=status,
            exchange=row,
            events=list(events or []),
            body=response_body if stream_complete else None,
        )
        return StreamAttempt(
            outbound_digest=_digest(raw),
            request_marker=marker,
            stream_id=stream_id,
            bytes_handed_off=handed,
            evidence=evidence,
            state=state,
            status=status,
            body=response_body,
            error_code=error_code,
        )


IMPLEMENTED_TOOLS: frozenset[str] = frozenset()


def register_operations(registry: dict[str, Operation] | None = None) -> None:
    """Register this module's operations idempotently; none are implemented yet."""


def open_muse_discussion(ctx: AppContext, request: m.MuseOpenRequest) -> m.Envelope:
    raise m.CoreError(m.ErrorCode.UNSUPPORTED, "not implemented in this build")


def submit_muse_exchange(ctx: AppContext, request: m.MuseSendRequest) -> m.Envelope:
    raise m.CoreError(m.ErrorCode.UNSUPPORTED, "not implemented in this build")


def read_muse_exchange(ctx: AppContext, request: m.MuseReadRequest) -> m.Envelope:
    raise m.CoreError(m.ErrorCode.UNSUPPORTED, "not implemented in this build")


def close_muse_exchange(ctx: AppContext, request: m.MuseCloseExchangeRequest) -> m.Envelope:
    raise m.CoreError(m.ErrorCode.UNSUPPORTED, "not implemented in this build")
