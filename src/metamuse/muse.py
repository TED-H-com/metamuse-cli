"""Muse transport and the feature seam.

B09 owns the gateway transport: HTTPS auth, one Noise_XX WebSocket, serial
unary calls on the four allowlisted routes, and the attempt evidence B10
persists. B10 lifecycle functions delegate to ``_muse_lifecycle``.

Nothing in this module is sent unless the caller passes ``network_mode="live"``,
a credential provider, and an outbound guard. Mutations also require a frozen
:class:`PreparedMuseRequest` and a :class:`SubmitAuthorization` whose
``consume`` is called once, before any byte is written, with
``safety.action_digest``. B10 binds that protocol to the core token minted
after ``before_external_action`` commits. See ``docs/dev/MUSE.md``.

``curl_cffi`` and ``noiseprotocol`` are imported by the adapter and the Noise
party on first use, not at import time.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import math
import re
import threading
import time
import urllib.parse
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
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
from metamuse._muse_codec import Header, ProtocolMismatch, json_depth, redact
from metamuse._muse_http import (
    AUTH_RESPONSE_MAX_BYTES,
    SESSION_RESPONSE_MAX_BYTES,
    Connector,
    CurlConnector,
    CurlHttp,
    HttpClient,
    HttpResponse,
    UpgradeFailure,
)
from metamuse._muse_link import (
    DEFAULT_COLLECT_TIMEOUT_S,
    IncompleteBody,
    OwnedLink,
    ReceiveLoop,
    SendUncertain,
)
from metamuse._muse_noise import NoiseParty, handshake_standard
from metamuse._muse_trust import SelectedVM, StoreMuseTrust, protocol_stop, storage_stop
from metamuse.models import CoreError, ErrorCode
from metamuse.safety import CheckedURL, action_digest
from metamuse.store import Store

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
    "PACKET_MAX_UTF8_BYTES",
    "PREPARED",
    "PROGRESS",
    "QUESTION_MAX_UTF8_BYTES",
    "REPLY_OBSERVED",
    "SEND_UNCERTAIN",
    "SENT",
    "SUBMITTING",
    "UNVERIFIED_UPSTREAM_COMPLETE",
    "VM_ADDRESS_TEMPLATE",
    "Association",
    "AttemptEvidence",
    "CredentialProvider",
    "Exchange",
    "GatewayResult",
    "MuseProgrammingError",
    "MuseProtocolStop",
    "MuseStateUnknown",
    "MuseTransport",
    "ObservationMemory",
    "OutboundGuard",
    "PreparedMuseRequest",
    "StreamAttempt",
    "SubmitAuthorization",
    "associate_reply",
    "attempt_evidence",
    "close_muse_exchange",
    "delivery_state",
    "normalize_event",
    "observe",
    "open_muse_discussion",
    "prepare_muse_request",
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

QUESTION_MAX_UTF8_BYTES = 8192
PACKET_MAX_UTF8_BYTES = 49152
_QUESTION_KEYS = ("question", "text")
_PACKET_KEYS = ("packet_text", "rendered_text")

_VM_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
# Legacy opaque alphabet: MUSE_SPIKE §2 / REPORT-2 §6.2; S1 uses s0 JWS,
# not a dotted legacy credential. Access grammar is kept separate.
_HATCH_LEGACY = re.compile(r"^[A-Za-z0-9_~+/=-]{1,4096}$")
_TOKEN = re.compile(r"^[A-Za-z0-9._~+/=-]{1,4096}$")
_MARKER = re.compile(r"^[A-Za-z0-9._~-]{1,128}$")
_HTTP_TIMEOUT_S = 20.0
_JSON_DEPTH = 32
_VM_STATES = frozenset(
    {
        "UNKNOWN",
        "CREATING",
        "RUNNING",
        "STOPPED",
        "DELETING",
        "ERROR",
        "SUSPENDED",
        "UPDATING",
        "DISABLED",
    }
)
_REPLACEMENT_KEYS = (
    "replace_attempt_id",
    "replacement_context",
    "replacement",
    "provisioning_pins",
)
_CONNECT_MAX_REDIALS = 20
_CONNECT_MAX_SECONDS = 240.0
_MUTATING = frozenset({"session.start", "chat.stream"})


class MuseProgrammingError(RuntimeError):
    """A route or argument the transport refuses before any I/O."""


class MuseStateUnknown(CoreError):
    """A create was handed off and the result was not read back."""

    def __init__(
        self,
        *,
        request_marker: str,
        outbound_digest: str,
        stream_id: int | None,
        bytes_handed_off: bool,
    ) -> None:
        super().__init__(
            ErrorCode.EXTERNAL_STATE_UNKNOWN,
            "session.start did not finish after the request was handed off",
            next_action=(
                "Reconcile with sessions.list by session_id. Do not start another session."
            ),
        )
        self.request_marker = request_marker
        self.outbound_digest = outbound_digest
        self.stream_id = stream_id
        self.bytes_handed_off = bytes_handed_off


class MuseNotSubmitted(CoreError):
    """Affirmative accounting: connect failed before any gateway request frame."""

    bytes_handed_off = False

    def __init__(self, error: CoreError | None = None):
        if error is None:
            super().__init__(ErrorCode.NETWORK_TIMEOUT, "Muse request failed before frame handoff")
        else:
            super().__init__(
                error.detail.code, error.detail.message, next_action=error.detail.next_action
            )


class MuseProtocolStop(CoreError):
    """Protocol failure after a mutation handoff. Delivery stays unknown."""

    def __init__(
        self,
        *,
        request_marker: str,
        outbound_digest: str,
        stream_id: int | None,
        bytes_handed_off: bool,
        attempt: StreamAttempt | None = None,
    ) -> None:
        super().__init__(
            ErrorCode.PROTOCOL_MISMATCH,
            "Muse protocol failed after the request was handed off",
            next_action="Stop. Delivery is unknown. Do not resend and do not weaken TLS.",
        )
        self.request_marker = request_marker
        self.outbound_digest = outbound_digest
        self.stream_id = stream_id
        self.bytes_handed_off = bytes_handed_off
        self.attempt = attempt


class CredentialProvider(Protocol):
    """B08 supplies the operator cookie jar for an opaque ``auth_ref``."""

    def cookies(self, auth_ref: str) -> str: ...


class OutboundGuard(Protocol):
    """B08 checked-URL seam. ``check`` never returns ``None``.

    Transport purposes are ``auth.check``, ``session``, ``hatch.token``, and
    ``wss.noise``. B10 maps every one of them to B08 ``purpose="request"``.
    The ``wss`` scheme is validated by B10 on an equivalent HTTPS URL, then
    restored on the :class:`CheckedURL` handed back here.
    """

    def check(self, url: str, *, purpose: str) -> CheckedURL: ...

    def verify_peer(self, checked: CheckedURL, peer_address: str) -> None: ...


class SubmitAuthorization(Protocol):
    """One-use proof that the external-action boundary committed.

    B10 binds this to the core ``ExternalActionToken``. The transport calls
    ``consume`` once with ``action_digest(target, payload)`` and sends nothing
    when that call fails. A bool or any object without ``consume`` is refused.
    """

    def consume(self, *, action_digest: str) -> None: ...


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


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise MuseProgrammingError("prepared request keys must be strings")
            frozen[key] = _freeze(item)
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MuseProgrammingError("prepared request has a non-finite number")
        return value
    raise MuseProgrammingError("prepared request value is not JSON")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _utf8_size(value: object) -> int | None:
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    return None


def _limit_error() -> CoreError:
    return CoreError(
        ErrorCode.LIMIT_REACHED,
        "Muse question or packet text exceeds the UTF-8 cap",
        next_action="Shorten the question to 8192 bytes or the packet text to 49152 bytes",
    )


def _joined_text(value: Mapping[str, Any]) -> str | None:
    question, packet = value.get("question"), value.get("packet_text")
    if isinstance(question, str) and isinstance(packet, str):
        if (
            len(question.encode()) > QUESTION_MAX_UTF8_BYTES
            or len(packet.encode()) > PACKET_MAX_UTF8_BYTES
        ):
            raise _limit_error()
        return question + "\n\n" + packet
    return None


def _check_mapping_limits(value: Mapping[str, Any]) -> None:
    _walk_limits(value, 0, _joined_text(value))


def _walk_limits(value: Any, depth: int, joined: str | None = None) -> None:
    """Apply the SPEC text caps at every object, including nested item text."""
    if depth > _JSON_DEPTH:
        raise _error(
            ErrorCode.PROTOCOL_MISMATCH,
            "Muse JSON nesting is too deep",
            next_action="Stop. Do not retry a damaged payload.",
        )
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in _QUESTION_KEYS:
                size = _utf8_size(item)
                if (
                    size is not None
                    and size > QUESTION_MAX_UTF8_BYTES
                    and not (key == "text" and joined is not None and item == joined)
                ):
                    raise _limit_error()
            if key in _PACKET_KEYS:
                size = _utf8_size(item)
                if size is not None and size > PACKET_MAX_UTF8_BYTES:
                    raise _limit_error()
            _walk_limits(item, depth + 1, joined)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _walk_limits(item, depth + 1, joined)


def _check_body_limits(body: bytes, joined: str | None = None) -> None:
    if not body:
        return
    if len(body) > AUTH_RESPONSE_MAX_BYTES and len(body) > PACKET_MAX_UTF8_BYTES + 1024:
        # The gateway body itself must fit a single frame. The text caps are
        # enforced on the fields; an enormous envelope is still refused here.
        if len(body) > 1024 * 1024:
            raise _limit_error()
    parsed = _json_value(body)
    if not isinstance(parsed, dict):
        raise MuseProgrammingError("prepared body is not a JSON object")
    _walk_limits(parsed, 0, joined)


def _json_value(body: bytes) -> Any:
    if json_depth(body, limit=_JSON_DEPTH) > _JSON_DEPTH:
        raise _error(
            ErrorCode.PROTOCOL_MISMATCH,
            "Muse JSON nesting is too deep",
            next_action="Stop. Do not retry a damaged payload.",
        )
    try:
        return json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError, ValueError, OverflowError):
        failure = _error(
            ErrorCode.PROTOCOL_MISMATCH,
            "Muse payload was not JSON",
            next_action="Stop. Do not retry a damaged payload.",
        )
    else:
        failure = None
    if failure is not None:
        raise failure
    return None


@dataclass(frozen=True)
class PreparedMuseRequest:
    """Immutable mutation built before authorization and before any I/O.

    ``body`` is the exact gateway JSON. ``target`` and ``payload`` are what
    ``action_digest`` covers. The marker is the caller's for both create and
    send; the transport does not mint one after ``consume``.
    """

    route: str
    verb: str
    path: str
    marker: str
    body: bytes = field(repr=False)
    target: Mapping[str, Any] = field(repr=False)
    payload: Mapping[str, Any] = field(repr=False)

    def __post_init__(self) -> None:
        spec = ROUTES.get(self.route)
        if spec is None or not spec.mutating or self.verb != spec.verb or self.path != spec.path:
            raise MuseProgrammingError("prepared request does not match a Muse mutation")
        if not isinstance(self.marker, str) or _MARKER.fullmatch(self.marker) is None:
            raise MuseProgrammingError("request marker is not usable")
        if not isinstance(self.body, (bytes, bytearray)):
            raise MuseProgrammingError("prepared body must be bytes")
        object.__setattr__(self, "body", bytes(self.body))
        object.__setattr__(self, "target", _freeze(self.target))
        object.__setattr__(self, "payload", _freeze(self.payload))
        _check_mapping_limits(self.payload)
        _check_body_limits(bytes(self.body), _joined_text(self.payload))


def prepare_muse_request(
    route: str,
    *,
    marker: str,
    body: Mapping[str, Any] | None = None,
    target: Mapping[str, Any] | None = None,
    payload: Mapping[str, Any] | None = None,
) -> PreparedMuseRequest:
    """Freeze a mutation. ``body`` bytes are canonical JSON of ``body``."""
    spec = ROUTES.get(route)
    if spec is None or not spec.mutating:
        raise MuseProgrammingError(f"route {route!r} is not a Muse mutation")
    document = dict(body or {})
    try:
        raw = m.canonical_json(document) if body is not None else b""
    except ValueError:
        raise MuseProgrammingError("request body is not a JSON object") from None
    if payload is None:
        payload = document
    if target is None:
        target = {"origin": MUSE_ORIGIN, "route": route}
    return PreparedMuseRequest(
        route=route,
        verb=spec.verb,
        path=spec.path,
        marker=marker,
        body=raw,
        target=dict(target),
        payload=payload,
    )


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
        detail = redact(self.evidence.detail)
        return (
            f"StreamAttempt(state={self.state!r}, verdict={self.evidence.verdict!r}, "
            f"bytes_handed_off={self.bytes_handed_off!r}, stream_id={self.stream_id!r}, "
            f"error_code={self.error_code!r}, detail={detail!r})"
        )


class _Secrets:
    """Ephemeral values seen on this call. Cleared before the call returns."""

    def __init__(self) -> None:
        self._values: list[str] = []

    def add(self, value: object) -> None:
        if isinstance(value, str) and len(value) >= 4 and value not in self._values:
            self._values.append(value)

    def text(self, value: str) -> str:
        # A bracketed placeholder makes B08's assignment matcher drop the closer.
        for secret in self._values:
            if secret and secret in value:
                value = value.replace(secret, "redacted")
        from metamuse._muse_redact import sanitize_text

        return sanitize_text(value, tuple(self._values))

    def body(self, raw: bytes) -> bytes:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return b""
        from metamuse._muse_redact import sanitize_document

        try:
            value = json.loads(text)
        except (ValueError, RecursionError):
            return self.text(text).encode("utf-8")
        return m.canonical_json(sanitize_document(value, tuple(self._values)))

    def clear(self) -> None:
        self._values.clear()


def _error(
    code: ErrorCode,
    message: str,
    *,
    next_action: str | None = None,
    retry_after: float = 0,
) -> CoreError:
    return CoreError(code, message, next_action=next_action, retry_after=retry_after)


def _raise_if(error: CoreError | None) -> None:
    if error is not None:
        raise error


# Messages the transport and the curl adapter already fixed. A caught provider
# error is re-raised as one of these, outside the handler, so its text and
# traceback chain are not the public error.
_PUBLIC_ERRORS: dict[str, tuple[ErrorCode, str]] = {
    "Muse session response exceeded the size cap": (
        ErrorCode.LIMIT_REACHED,
        "Stop. Do not parse an oversized session response.",
    ),
    "Muse confidentiality could not be recorded": (
        ErrorCode.STORAGE,
        "Repair the security-state store before reconnecting; confidentiality could not be recorded",
    ),
    "Muse minted credentials remain stale": (
        ErrorCode.RESOURCE_UNAVAILABLE,
        "Retry the connection later; minted credentials remain stale",
    ),
    "Outbound peer was rejected": (
        ErrorCode.SCOPE_REQUIRED,
        "Do not accept a response from an address outside the pin.",
    ),
    "Outbound peer was not available": (
        ErrorCode.SCOPE_REQUIRED,
        "Do not accept a response until the peer address matches the pin.",
    ),
    "Outbound pin does not match the requested URL": (
        ErrorCode.SCOPE_REQUIRED,
        "Pin the canonical hostname. Do not pin a different host.",
    ),
    "Outbound host must stay a hostname": (
        ErrorCode.SCOPE_REQUIRED,
        "Keep Host and SNI on the hostname. Do not substitute an IP.",
    ),
    "Pinned addresses were empty": (
        ErrorCode.SCOPE_REQUIRED,
        "Return at least one address from the outbound guard",
    ),
    "Muse gateway URL is not a wss URL": (
        ErrorCode.SCOPE_REQUIRED,
        "Use the hatch WebSocket endpoint.",
    ),
    "curl_cffi is not installed": (
        ErrorCode.DEPENDENCY_MISSING,
        "Install the pinned curl_cffi package. Do not add websockets.",
    ),
    "curl_cffi API is not the pinned shape": (
        ErrorCode.DEPENDENCY_MISSING,
        "Install curl_cffi==0.16.3. Do not retry this process.",
    ),
    "noiseprotocol is not installed": (
        ErrorCode.DEPENDENCY_MISSING,
        "Install the pinned noiseprotocol package. Do not hand-roll Noise.",
    ),
    "certifi is not installed": (
        ErrorCode.DEPENDENCY_MISSING,
        "Install the pinned certifi bundle. Do not trust an environment CA path.",
    ),
    "certifi did not provide a CA bundle": (
        ErrorCode.DEPENDENCY_MISSING,
        "Install the pinned certifi bundle.",
    ),
    "Muse auth response exceeded the size cap": (
        ErrorCode.LIMIT_REACHED,
        "Stop. Do not parse an oversized auth response.",
    ),
    "Muse HTTPS request failed": (
        ErrorCode.NETWORK_TIMEOUT,
        "Retry the auth request. No gateway frame was handed off.",
    ),
}

_PUBLIC_CODES: dict[ErrorCode, tuple[str, str]] = {
    ErrorCode.STORAGE: (
        "Muse security state is unavailable or stale",
        "Restore the bound account security-state store before reconnecting",
    ),
    ErrorCode.RESOURCE_UNAVAILABLE: (
        "Muse VM is not running yet",
        "The VM is not running yet; retry later",
    ),
    ErrorCode.RATE_LIMITED: (
        "Muse rate limit",
        "Wait before reconnecting; do not resend a handed-off chat",
    ),
    ErrorCode.DEPENDENCY_MISSING: (
        "A Muse dependency is not available",
        "Install the pinned curl_cffi and noiseprotocol packages.",
    ),
    ErrorCode.PROTOCOL_MISMATCH: (
        "Muse protocol failed",
        "Stop. Do not retry this frame or weaken TLS.",
    ),
    ErrorCode.SCOPE_REQUIRED: (
        "Outbound connection was refused",
        "Approve the Muse URL through the outbound guard.",
    ),
    ErrorCode.LIMIT_REACHED: (
        "Muse auth response exceeded the size cap",
        "Stop. Do not parse an oversized auth response.",
    ),
    ErrorCode.NETWORK_TIMEOUT: (
        "Muse connection failed",
        "Retry the connection. No gateway request frame was sent.",
    ),
    ErrorCode.AUTH_REQUIRED: (
        "Muse credentials are unavailable",
        "Refresh the operator cookie jar for this auth_ref",
    ),
}


def _sanitize_transport_error(exc: CoreError) -> CoreError:
    """Fixed public error. The provider text is not copied."""
    known = _PUBLIC_ERRORS.get(exc.detail.message)
    if known is not None:
        code, action = known
        return _error(code, exc.detail.message, next_action=action)
    fallback = _PUBLIC_CODES.get(exc.detail.code)
    if fallback is not None:
        message, action = fallback
        return _error(exc.detail.code, message, next_action=action)
    return _error(
        ErrorCode.NETWORK_TIMEOUT,
        "Muse connection failed",
        next_action="Retry the connection. No gateway request frame was sent.",
    )


def _emit(value: GatewayResult | StreamAttempt | BaseException) -> GatewayResult | StreamAttempt:
    """Raise a sanitized error outside the handler that built it."""
    if isinstance(value, BaseException):
        raise value
    return value


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _retry_after(headers: Mapping[str, str]) -> float:
    for key, value in headers.items():
        if str(key).lower() != "retry-after":
            continue
        if isinstance(value, str) and len(value) <= 6 and value.isdigit():
            return float(int(value))
        return 0
    return 0


def _json_object(body: bytes, *, session: bool = False) -> dict[str, Any]:
    if len(body) > (SESSION_RESPONSE_MAX_BYTES if session else AUTH_RESPONSE_MAX_BYTES):
        raise _error(
            ErrorCode.LIMIT_REACHED,
            "Muse session response exceeded the size cap"
            if session
            else "Muse auth response exceeded the size cap",
            next_action="Stop. Do not parse an oversized session response."
            if session
            else "Stop. Do not parse an oversized auth response.",
        )
    value = _json_value(body)
    if not isinstance(value, dict):
        raise _error(
            ErrorCode.PROTOCOL_MISMATCH,
            "Muse HTTPS response was not an object",
            next_action="Stop. Do not retry a damaged auth response.",
        )
    return value


def _budget(timeout_s: float) -> float:
    if (
        isinstance(timeout_s, bool)
        or not isinstance(timeout_s, (int, float))
        or not math.isfinite(timeout_s)
        or timeout_s < 0
        or timeout_s > DEFAULT_COLLECT_TIMEOUT_S
    ):
        raise MuseProgrammingError("collect timeout exceeds the cap")
    return float(timeout_s)


def _wss_url(vm_id: str, token: str, notary: str | None = None) -> str:
    query = {"vm_id": vm_id, "auth_token": token}
    if notary:
        query["notary_token"] = notary
    query.update(app_id="hatch-web", request_id=str(uuid.uuid4()))
    return HATCH_WSS_BASE + "?" + urllib.parse.urlencode(query)


def _credential_shape(value: Any) -> None:
    if isinstance(value, str):
        trimmed = value.strip()
        if trimmed.lower().startswith(("delegation.", "endorsement.")):
            raise protocol_stop()
        if trimmed.lower().startswith("s0:") and (
            len(value) > 4096
            or re.fullmatch(r"s0:[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", value) is None
        ):
            raise protocol_stop()


def _hatch_token(value: Any) -> bool:
    _credential_shape(value)
    if not isinstance(value, str) or len(value) > 4096:
        return False
    if value.startswith("s0:"):
        return re.fullmatch(r"s0:[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", value) is not None
    return _HATCH_LEGACY.fullmatch(value) is not None


def _notary_token(value: Any) -> str | None:
    _credential_shape(value)
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) > 4096
        or any(ord(c) < 32 or ord(c) >= 127 for c in value)
    ):
        raise protocol_stop()
    value = value.strip()
    if not value:
        return None
    if (
        value.lower().startswith(("delegation.", "endorsement.", "s0:"))
        or re.fullmatch(r"(?:[A-Za-z0-9_-]+\.){5}[A-Za-z0-9_-]+", value) is None
    ):
        raise protocol_stop()
    return value


def _notary_stale(value: str | None, now: float) -> bool:
    if value is None:
        return False
    try:
        segment = value.split(".")[2]
        document = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
        restrictions = document.get("restrictions")
        if not isinstance(restrictions, list):
            return False
        for item in restrictions:
            if isinstance(item, str) and item.startswith("timeout:"):
                expiry = float(item[len("timeout:") :].strip())
                return math.isfinite(expiry) and expiry > 0 and expiry <= now + 60
    except (ValueError, TypeError, AttributeError, UnicodeError):
        pass
    return False


def _apply_query(path: str, query: Mapping[str, Any]) -> str:
    clean = {
        key: value for key, value in query.items() if value is not None and key != "transcript_mode"
    }
    if not clean:
        return path
    return path + "?" + urllib.parse.urlencode(clean)


def _exchange_for(
    body: Mapping[str, Any] | None, marker: str, exchange: Exchange | None
) -> Exchange:
    if exchange is not None:
        return exchange
    text = ""
    if isinstance(body, Mapping):
        items = body.get("items")
        if isinstance(items, tuple) and items and isinstance(items[0], Mapping):
            raw_text = items[0].get("text")
            if isinstance(raw_text, str):
                text = raw_text
        elif isinstance(body.get("text"), str):
            text = str(body["text"])
    return Exchange(session_id="", baseline_seq=0, sent_text=text, request_marker=marker)


class MuseTransport:
    """One gateway connection. One receive loop. Serial unary calls.

    Defaults refuse. ``network_mode="offline"`` refuses with ``E_SCOPE_DENIED``.
    A missing credential provider refuses with ``E_AUTH_REQUIRED``. A missing
    outbound guard refuses with ``E_SCOPE_REQUIRED``. Mutations refuse until
    ``consume`` succeeds on the caller's authorization.
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
        store: Store | None = None,
        trust: StoreMuseTrust | None = None,
        sleep: Callable[[float], None] | None = None,
        wall_clock: Callable[[], float] | None = None,
    ) -> None:
        if proxy is not None:
            raise _error(
                ErrorCode.SCOPE_REQUIRED,
                "Explicit proxies are not available",
                next_action="Connect directly. No guard-validated proxy exists.",
            )
        self._auth_ref = auth_ref
        self._credentials = credentials
        self._guard = guard
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._wall_clock = wall_clock or time.time
        binding = getattr(credentials, "binding", None)
        self._trust = trust or (
            StoreMuseTrust(store, binding.provider, binding.account)
            if store is not None and binding is not None
            else None
        )
        self._snapshot = None
        self._trust_invalidated = False
        self._network_mode = network_mode
        self._http = http if http is not None else CurlHttp()
        self._connector = connector if connector is not None else CurlConnector()
        self._lock = threading.Lock()
        self._loop: ReceiveLoop | None = None
        self._secrets = _Secrets()
        self._connection_secrets: tuple[str, ...] = ()

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
        self._lock.acquire()
        try:
            self._close_locked()
        finally:
            self._lock.release()

    def _close_locked(self) -> None:
        loop = self._loop
        self._loop = None
        self._connection_secrets = ()
        if loop is None:
            return
        loop.__exit__(None, None, None)
        loop.link.close()

    def session_start(
        self,
        prepared: PreparedMuseRequest,
        *,
        authorization: SubmitAuthorization,
        timeout_s: float = DEFAULT_COLLECT_TIMEOUT_S,
    ) -> GatewayResult:
        result = self.call(
            "session.start",
            prepared=prepared,
            authorization=authorization,
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
        prepared: PreparedMuseRequest,
        *,
        authorization: SubmitAuthorization,
        timeout_s: float = DEFAULT_COLLECT_TIMEOUT_S,
        exchange: Exchange | None = None,
        events: list[dict[str, Any]] | None = None,
    ) -> StreamAttempt:
        result = self.call(
            "chat.stream",
            prepared=prepared,
            authorization=authorization,
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
        prepared: PreparedMuseRequest | None = None,
        authorization: SubmitAuthorization | None = None,
        query: Mapping[str, Any] | None = None,
        timeout_s: float = DEFAULT_COLLECT_TIMEOUT_S,
        exchange: Exchange | None = None,
        events: list[dict[str, Any]] | None = None,
    ) -> GatewayResult | StreamAttempt:
        spec = ROUTES.get(route)
        if spec is None:
            raise MuseProgrammingError(f"route {route!r} is not on the Muse allowlist")
        timeout_s = _budget(timeout_s)
        self._ready()
        if spec.mutating:
            if not isinstance(prepared, PreparedMuseRequest) or prepared.route != route:
                raise MuseProgrammingError("mutation requires a prepared request for this route")
            if (
                exchange is not None
                and exchange.request_marker
                and exchange.request_marker != prepared.marker
            ):
                raise MuseProgrammingError("exchange marker does not match the prepared request")
            self._authorize(authorization, prepared)
            marker = prepared.marker
            raw = prepared.body
            payload: Mapping[str, Any] | None = prepared.payload
        else:
            if prepared is not None or authorization is not None:
                raise MuseProgrammingError("reads do not take a mutation authorization")
            marker = str(uuid.uuid4())
            raw = b""
            payload = None
        if not self._lock.acquire(blocking=False):
            raise ProtocolMismatch("concurrent collection")
        self._secrets = _Secrets()
        for secret in self._connection_secrets:
            self._secrets.add(secret)
        try:
            connect_error = None
            try:
                self._connect()
            except CoreError as exc:
                connect_error = exc
            if connect_error is not None and spec.mutating:
                if route == "chat.stream":
                    from dataclasses import replace

                    return replace(
                        self._never_sent(marker, raw), error_code=connect_error.detail.code.value
                    )
                raise MuseNotSubmitted(connect_error)
            if connect_error is not None:
                raise connect_error
            return self._unary(
                route,
                spec,
                raw=raw,
                path=(
                    prepared.path if prepared is not None else _apply_query(spec.path, query or {})
                ),
                verb=prepared.verb if prepared is not None else spec.verb,
                timeout_s=timeout_s,
                exchange=exchange,
                events=events,
                marker=marker,
                payload=payload,
            )
        finally:
            self._secrets.clear()
            self._lock.release()

    def _ready(self) -> None:
        if self._network_mode == "offline":
            raise _error(
                ErrorCode.SCOPE_DENIED,
                "External operations are disabled in offline mode",
                next_action="Enable live mode explicitly and configure the required permission",
            )
        if self._network_mode != "live":
            raise MuseProgrammingError("network_mode must be offline or live")
        if self._credentials is None or not self._auth_ref:
            raise _error(
                ErrorCode.AUTH_REQUIRED,
                "No credential provider is configured",
                next_action="Inject a credential provider that returns the cookie jar for auth_ref",
            )
        if self._guard is None:
            raise _error(
                ErrorCode.SCOPE_REQUIRED,
                "No outbound URL guard is configured",
                next_action="Inject an outbound URL guard before any Muse request",
            )

    def _authorize(self, authorization: object, prepared: PreparedMuseRequest) -> None:
        consume = getattr(authorization, "consume", None)
        if authorization is None or isinstance(authorization, bool) or not callable(consume):
            raise _error(
                ErrorCode.SCOPE_REQUIRED,
                "Muse mutations require a submit authorization",
                next_action=(
                    "Call before_external_action and pass the committed token. "
                    "Do not pass a boolean."
                ),
            )
        digest = action_digest(_thaw(prepared.target), _thaw(prepared.payload))
        failure: CoreError | None = None
        try:
            consume(action_digest=digest)
        except Exception:
            failure = _error(
                ErrorCode.SCOPE_REQUIRED,
                "Submit authorization was refused",
                next_action="Mint a new token only after a new committed boundary.",
            )
        _raise_if(failure)

    def _remember(self, value: object) -> None:
        self._secrets.add(value)

    def _cookie_jar(self) -> str:
        provider = self._credentials
        if provider is None:
            raise _error(
                ErrorCode.AUTH_REQUIRED,
                "No credential provider is configured",
                next_action="Inject a credential provider that returns the cookie jar for auth_ref",
            )
        failure: CoreError | None = None
        jar = ""
        try:
            jar = provider.cookies(self._auth_ref)
        except Exception:
            failure = _error(
                ErrorCode.AUTH_REQUIRED,
                "Muse credentials are unavailable",
                next_action="Refresh the operator cookie jar for this auth_ref",
            )
            jar = ""
        _raise_if(failure)
        if (
            not isinstance(jar, str)
            or not jar
            or any(ord(char) < 0x20 or ord(char) == 0x7F for char in jar)
        ):
            raise _error(
                ErrorCode.AUTH_REQUIRED,
                "Credential jar is not usable",
                next_action="Return one cookie header value with no control characters",
            )
        self._remember(jar)
        return jar

    def _check_url(self, url: str, purpose: str) -> CheckedURL:
        guard = self._guard
        if guard is None:
            raise _error(
                ErrorCode.SCOPE_REQUIRED,
                "No outbound URL guard is configured",
                next_action="Inject an outbound URL guard before any Muse request",
            )
        failure: CoreError | None = None
        checked: CheckedURL | None = None
        try:
            checked = guard.check(url, purpose=purpose)
        except Exception:
            failure = _error(
                ErrorCode.SCOPE_REQUIRED,
                "Outbound URL check failed",
                next_action="Approve the Muse URL through the outbound guard",
            )
        _raise_if(failure)
        if not isinstance(checked, CheckedURL) or not checked.addresses:
            raise _error(
                ErrorCode.SCOPE_REQUIRED,
                "Outbound URL check did not return pinned addresses",
                next_action="Return the canonical URL and at least one address",
            )
        requested = urllib.parse.urlsplit(url)
        req_host = (requested.hostname or "").rstrip(".").lower()
        req_port = requested.port or (443 if requested.scheme in {"https", "wss"} else 80)
        canonical = urllib.parse.urlsplit(checked.url)
        if (
            checked.hostname.lower() != req_host
            or checked.port != req_port
            or canonical.scheme != requested.scheme
            or (canonical.hostname or "").lower() != req_host
            or (canonical.port or 443) != req_port
            or canonical.path != requested.path
            or canonical.query != requested.query
            or canonical.fragment
            or canonical.username
            or canonical.password
        ):
            raise _error(
                ErrorCode.SCOPE_REQUIRED,
                "Outbound pin does not match the requested URL",
                next_action="Pin the canonical hostname. Do not pin a different host.",
            )
        try:
            ipaddress.ip_address(checked.hostname)
        except ValueError:
            ip_host = False
        else:
            ip_host = True
        if ip_host:
            raise _error(
                ErrorCode.SCOPE_REQUIRED,
                "Outbound host must stay a hostname",
                next_action="Keep Host and SNI on the hostname. Do not substitute an IP.",
            )
        return checked

    def _verify_peer(self, checked: CheckedURL, peer: str) -> None:
        guard = self._guard
        if guard is None or not isinstance(peer, str) or not peer:
            failure = _error(
                ErrorCode.SCOPE_REQUIRED,
                "Outbound peer was not available",
                next_action="Do not accept a response until the peer address matches the pin.",
            )
        else:
            failure = None
            try:
                guard.verify_peer(checked, peer)
            except Exception:
                failure = _error(
                    ErrorCode.SCOPE_REQUIRED,
                    "Outbound peer was rejected",
                    next_action="Do not accept a response from an address outside the pin.",
                )
        _raise_if(failure)

    def _https(
        self,
        method: str,
        path: str,
        *,
        cookies: str,
        body: bytes | None,
        purpose: str,
        bearer: str | None = None,
        timeout: float = _HTTP_TIMEOUT_S,
        connect_deadline: float | None = None,
    ) -> dict[str, Any]:
        url = MUSE_ORIGIN + path
        checked = self._check_url(url, purpose)
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
        failure: CoreError | None = None
        response: HttpResponse | None = None
        if connect_deadline is not None:
            timeout = self._remaining_connect_budget(connect_deadline, timeout)
        try:
            response = self._http.request(
                method,
                checked.url,
                headers=headers,
                body=body,
                checked=checked,
                timeout=timeout,
            )
        except CoreError as exc:
            failure = _sanitize_transport_error(exc)
        except Exception:
            failure = _error(
                ErrorCode.NETWORK_TIMEOUT,
                "Muse HTTPS request failed",
                next_action="Retry the auth request. No gateway frame was handed off.",
            )
        _raise_if(failure)
        if response is None:
            raise MuseProgrammingError("https request did not return")
        self._verify_peer(checked, response.peer)
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
        if status >= 500 or (
            status in (404, 409, 410)
            and path in ("/api/session?vm_state=live", "/api/hatch/vm/wake")
        ):
            raise _error(
                ErrorCode.RESOURCE_UNAVAILABLE,
                "The Muse HTTPS endpoint is unavailable",
                next_action="Retry later. This is not an authentication failure.",
            )
        # Success: spike §2/§5 bootstrap 200; S1 §Finding 3 wake 200.
        # Wake 202/204 are explicitly admitted by the round-2 operator contract;
        # they indicate accepted/no-content activation, not a running VM.
        accepted = {200, 202, 204} if path == "/api/hatch/vm/wake" else {200}
        if status not in accepted:
            raise protocol_stop()
        if path == "/api/hatch/vm/wake":
            return {}
        payload = _json_object(response.body, session=path == "/api/session?vm_state=live")
        if path == "/api/session?vm_state=live":
            return payload
        if path == "/api/auth/check":
            token = payload.get("access_token")
            if payload.get("ok") is False:
                raise _error(ErrorCode.AUTH_REQUIRED, "Muse authentication failed")
            if (
                payload.get("ok") is not True
                or not isinstance(token, str)
                or _TOKEN.fullmatch(token) is None
            ):
                raise protocol_stop()
            self._remember(token)
            context = {}
            for key in ("viewer_id", "session_binding_id"):
                value = payload.get(key)
                if (
                    not isinstance(value, str)
                    or not 1 <= len(value) <= 4096
                    or any(ord(c) < 33 or ord(c) >= 127 for c in value)
                ):
                    raise protocol_stop()
                self._remember(value)
                context[key] = value
            return {"access_token": token, **context}
        if path == "/api/hatch/token":
            fields = ("token", "notary_token", "notaryToken", "noiseNotaryToken", "endorsement")
            for key in fields:
                if key in payload:
                    self._remember(payload[key])
                    _credential_shape(payload[key])
            token = payload.get("token")
            if not _hatch_token(token):
                raise protocol_stop()
            aliases = [_notary_token(payload[key]) for key in fields[1:] if key in payload]
            present = {value for value in aliases if value is not None}
            if len(present) > 1:
                raise protocol_stop()
            return {"token": token, "notary_token": next(iter(present), None)}
        raise MuseProgrammingError("unexpected Muse HTTPS path")

    def _session_payload(self, payload: dict[str, Any], auth: dict[str, Any]) -> dict[str, Any]:
        rows = payload.get("vms")
        items = [payload] + (
            [item for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []
        )

        def identity(item):
            value = item.get("vm_id")
            return (
                value.strip().lower()
                if isinstance(value, str) and _VM_ID.fullmatch(value.strip())
                else None
            )

        # Scan every authenticated row before status, uniqueness or routing checks.
        ids = [identity(item) for item in items]
        confidential = any(
            item.get("vm_type") == "hatch_cvm"
            or isinstance(item.get("vm_state"), str)
            and item["vm_state"].startswith("CVM_")
            or item.get("cvm_required")
            or item.get("cvmReplacementEnabled")
            for item in items
        )
        if confidential:
            usable = tuple(sorted({value for value in ids if value is not None}))
            self._trust.store.muse_freeze(
                provider=self._trust.provider,
                account=self._trust.account,
                vm_id=ids[0] or (usable[0] if usable else None),
                viewer_id=auth["viewer_id"],
                session_binding_id=auth["session_binding_id"],
                confidential=True,
                confidential_vm_ids=usable,
            )
            raise protocol_stop()
        vm_id = payload.get("vm_id")
        normalized = ids[0]
        if payload.get("status") != "assigned" or vm_id is None:
            raise _error(
                ErrorCode.RESOURCE_UNAVAILABLE,
                "The Muse VM is unavailable",
                next_action="Retry later",
            )
        if normalized is None:
            raise protocol_stop()
        row = payload
        if rows is not None:
            if not isinstance(rows, list) or any(not isinstance(item, dict) for item in rows):
                raise protocol_stop()
            matches = [item for item in rows if identity(item) == normalized]
            if len(matches) != 1:
                raise protocol_stop()
            row = matches[0]
        # Retain exact server ID on the wire; equality uses its normalized identity.
        if rows is not None:
            for key in ("vm_type", "vm_state", "endpoint_url"):
                if key in payload and key in row and payload[key] != row[key]:
                    raise protocol_stop()
        name = row.get("name", row.get("vm_name"))
        if (
            "name" in row
            and "vm_name" in row
            and (
                not isinstance(row["name"], str)
                or not isinstance(row["vm_name"], str)
                or row["name"].strip().lower() != row["vm_name"].strip().lower()
            )
        ):
            raise protocol_stop()
        if "vm_name" in payload and (
            not isinstance(payload["vm_name"], str)
            or not isinstance(name, str)
            or payload["vm_name"].strip().lower() != name.strip().lower()
        ):
            raise protocol_stop()
        endpoint = row.get("endpoint_url")
        vm_type, state = row.get("vm_type"), row.get("vm_state")
        if (
            not isinstance(name, str)
            or name.strip().lower() != normalized
            or not isinstance(endpoint, str)
            or endpoint.lower() != VM_ADDRESS_TEMPLATE.format(vm_id=normalized)
            or vm_type not in ("hatch_vm", "hatch_cvm")
            or not isinstance(state, str)
            or (state not in _VM_STATES and not state.startswith("CVM_"))
            or payload.get("vm_resolution_issue")
        ):
            raise protocol_stop()
        replacement = any(item.get(key) for item in (payload, row) for key in _REPLACEMENT_KEYS)
        return {"selected": SelectedVM(vm_id, name, endpoint, vm_type, state, replacement)}

    def _remaining_connect_budget(self, deadline: float, cap: float) -> float:
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise UpgradeFailure(503)
        return min(cap, remaining)

    def _remember_nonce(self, nonce: bytes) -> None:
        for value in (
            nonce.hex(),
            base64.b64encode(nonce).decode(),
            base64.urlsafe_b64encode(nonce).decode(),
            base64.urlsafe_b64encode(nonce).decode().rstrip("="),
        ):
            self._remember(value)

    def _wake(
        self,
        selected: SelectedVM,
        jar: str,
        access: str,
        retry: int,
        timeout: float,
        *,
        deadline: float,
    ) -> None:
        document = {"vm_id": selected.vm_id, "retry_count": retry}
        if retry:
            document["connect_attempt_id"] = str(uuid.uuid4())
            self._remember(document["connect_attempt_id"])
        body = m.canonical_json(document)
        self._remember(body.decode())
        self._https(
            "POST",
            "/api/hatch/vm/wake",
            cookies=jar,
            body=body,
            purpose="hatch.wake",
            connect_deadline=deadline,
            bearer=access,
            timeout=timeout,
        )

    def _mint(self, selected: SelectedVM, jar: str, access: str) -> dict[str, Any]:
        for _ in range(2):
            hatch = self._https(
                "POST",
                "/api/hatch/token",
                cookies=jar,
                body=m.canonical_json(
                    {"vmAddress": selected.endpoint_url, "vmName": selected.name}
                ),
                purpose="hatch.token",
                bearer=access,
            )
            if not _notary_stale(hatch.get("notary_token"), self._wall_clock()):
                return hatch
        # REPORT-2 §6.2 specifies unavailable after one remint; neither it nor
        # S1 establishes that refreshing a session repairs a twice-stale notary.
        raise _error(
            ErrorCode.RESOURCE_UNAVAILABLE,
            "Muse minted credentials remain stale",
            next_action="Retry the connection later; minted credentials remain stale",
        )

    def _invalidate_trust(self) -> None:
        self._trust_invalidated = True
        self._connection_secrets = ()
        if self._loop is not None:
            self._loop.link.close()

    def _connect(self) -> None:
        if self._loop is not None:
            try:
                self._trust.assert_current(self._snapshot)
            except CoreError:
                self._close_locked()
                raise
            if self._trust_invalidated:
                self._close_locked()
                raise storage_stop()
            return
        jar = self._cookie_jar()
        if self._trust is None:
            raise storage_stop()
        auth = self._https("POST", "/api/auth/check", cookies=jar, body=b"", purpose="auth.check")
        access = auth["access_token"]
        session = self._https(
            "GET", "/api/session?vm_state=live", cookies=jar, body=None, purpose="session"
        )
        selected = self._session_payload(session, auth)["selected"]
        snapshot = self._trust.freeze(auth, selected)
        if snapshot.confidential_required or selected.replacement:
            raise protocol_stop()
        if selected.vm_state in ("DISABLED", "DELETING", "ERROR"):
            raise _error(
                ErrorCode.RESOURCE_UNAVAILABLE,
                "The Muse VM is unavailable",
                next_action="The VM is not running; retry later",
            )
        self._trust.assert_current(snapshot)
        hatch = self._mint(selected, jar, access)
        deadline = self._clock() + _CONNECT_MAX_SECONDS
        self._wake(
            selected,
            jar,
            access,
            0,
            self._remaining_connect_budget(deadline, _HTTP_TIMEOUT_S),
            deadline=deadline,
        )
        raw: Any = None
        loop: ReceiveLoop | None = None
        failure: CoreError | None = None
        try:
            for attempt in range(_CONNECT_MAX_REDIALS + 1):
                self._trust.assert_current(snapshot)
                if self._clock() >= deadline:
                    raise UpgradeFailure(503)
                url = _wss_url(selected.vm_id, hatch["token"], hatch.get("notary_token"))
                self._remember(
                    urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["request_id"][0]
                )
                checked = self._check_url(url, "wss.noise")
                upgrade = None
                try:
                    raw = self._connector.connect(
                        checked.url,
                        checked=checked,
                        timeout=self._remaining_connect_budget(deadline, _HTTP_TIMEOUT_S),
                    )
                except UpgradeFailure as exc:
                    upgrade = exc
                if upgrade is None:
                    if self._clock() >= deadline:
                        raise UpgradeFailure(503)
                    break
                if upgrade.status not in (502, 503, 504):
                    raise upgrade
                n = attempt + 1
                delay = max(2.0, min(0.2 * 1.5 ** (n - 1), 8.0))
                if attempt >= _CONNECT_MAX_REDIALS or self._clock() + delay >= deadline:
                    raise UpgradeFailure(upgrade.status)
                self._trust.assert_current(snapshot)
                self._wake(
                    selected,
                    jar,
                    access,
                    n,
                    self._remaining_connect_budget(deadline, _HTTP_TIMEOUT_S),
                    deadline=deadline,
                )
                if self._clock() + delay >= deadline:
                    raise UpgradeFailure(upgrade.status)
                self._sleep(delay)
            self._verify_peer(checked, str(getattr(raw, "peer_address", "")))
            party = NoiseParty("initiator")
            owned = OwnedLink(raw)
            loop = ReceiveLoop(owned, noise=party, clock=self._clock)
            loop.__enter__()
            handshake_standard(
                party,
                owned,
                timeout=self._remaining_connect_budget(deadline, DEFAULT_COLLECT_TIMEOUT_S),
                remember_nonce=self._remember_nonce,
                clock=self._clock,
                confidential=lambda: self._trust.confidential(snapshot),
                standard_handoff=lambda handoff: self._trust.standard_handoff(snapshot, handoff),
            )
            self._connection_secrets = tuple(self._secrets._values)
            self._loop, self._snapshot = loop, snapshot
            self._trust_invalidated = False
            self._trust.store.muse_watch(snapshot, self._invalidate_trust)
            self._trust.assert_current(snapshot)
        except IncompleteBody:
            failure = (
                UpgradeFailure(503)
                if self._clock() >= deadline
                else _error(
                    ErrorCode.NETWORK_TIMEOUT,
                    "Muse handshake timed out",
                    next_action="Retry the connection; no application frame was sent",
                )
            )
        except ProtocolMismatch:
            failure = protocol_stop()
        except CoreError as exc:
            failure = _sanitize_transport_error(exc)
        except Exception:
            failure = _error(
                ErrorCode.NETWORK_TIMEOUT,
                "Muse connection failed",
                next_action="Retry the connection; no application frame was sent",
            )
        else:
            return
        self._loop = None
        self._drop(loop, raw)
        _raise_if(failure)

    def _drop(self, loop: ReceiveLoop | None, raw: Any) -> None:
        if loop is not None:
            loop.__exit__(None, None, None)
            loop.link.close()
            return
        if raw is not None:
            try:
                raw.close()
            except Exception:
                pass

    def _unary(
        self,
        route: str,
        spec: _Route,
        *,
        raw: bytes,
        path: str,
        verb: str,
        timeout_s: float,
        exchange: Exchange | None,
        events: list[dict[str, Any]] | None,
        marker: str,
        payload: Mapping[str, Any] | None,
    ) -> GatewayResult | StreamAttempt:
        headers = [Header("x-request-id", marker), Header("accept-encoding", "identity")]
        if raw:
            headers.append(Header("content-type", "application/json"))
        loop = self._loop
        if loop is None:
            raise MuseProgrammingError("gateway connection is not open")
        stream_id: int | None = None
        result: GatewayResult | StreamAttempt | BaseException
        try:
            stream_id = loop.open_request(verb=verb, path=path, body=raw, headers=headers)
            status, response_body = loop.collect(stream_id, timeout_s=timeout_s)
        except IncompleteBody as exc:
            self._abandon(loop)
            result = self._incomplete(route, exc, loop, marker, raw, payload, exchange, events)
        except ProtocolMismatch:
            self._abandon(loop)
            result = self._from_code(
                route,
                ErrorCode.PROTOCOL_MISMATCH,
                loop.handed_off,
                loop,
                marker,
                raw,
                payload,
                exchange,
                events,
            )
        except SendUncertain:
            self._abandon(loop)
            result = self._after_uncertain_send(route, loop, marker, raw, payload, exchange, events)
        except CoreError as exc:
            code = exc.detail.code
            handed = loop.handed_off
            self._abandon(loop)
            result = self._from_code(
                route, code, handed, loop, marker, raw, payload, exchange, events
            )
        except Exception:
            handed = loop.handed_off
            self._abandon(loop)
            if route == "chat.stream" and not handed:
                result = self._never_sent(marker, raw)
            elif handed and route in _MUTATING:
                result = self._from_code(
                    route,
                    ErrorCode.EXTERNAL_STATE_UNKNOWN,
                    True,
                    loop,
                    marker,
                    raw,
                    payload,
                    exchange,
                    events,
                )
            else:
                result = _error(
                    ErrorCode.NETWORK_TIMEOUT,
                    "Muse read failed",
                    next_action="Retry the read if no frame was handed off.",
                )
        else:
            public = self._secrets.body(response_body)
            if route == "chat.stream":
                result = self._stream_attempt(
                    status=status,
                    response_body=response_body,
                    stream_id=stream_id,
                    marker=marker,
                    raw=raw,
                    payload=payload,
                    exchange=exchange,
                    events=events,
                    handed=True,
                    error_code=None,
                    public_body=public,
                )
            else:
                result = GatewayResult(
                    route=route,
                    status=status,
                    body=public,
                    stream_id=stream_id,
                    request_marker=marker,
                    outbound_digest=_digest(raw),
                    bytes_handed_off=True,
                )
        return _emit(result)

    def _abandon(self, loop: ReceiveLoop) -> None:
        if loop.failed:
            self._close_locked()

    def _from_code(
        self,
        route: str,
        code: ErrorCode,
        handed: bool,
        loop: ReceiveLoop,
        marker: str,
        raw: bytes,
        payload: Mapping[str, Any] | None,
        exchange: Exchange | None,
        events: list[dict[str, Any]] | None,
    ) -> GatewayResult | StreamAttempt | CoreError:
        if route == "session.start" and handed:
            if code == ErrorCode.PROTOCOL_MISMATCH:
                return MuseProtocolStop(
                    request_marker=marker,
                    outbound_digest=_digest(raw),
                    stream_id=loop.last_stream_id,
                    bytes_handed_off=True,
                )
            return MuseStateUnknown(
                request_marker=marker,
                outbound_digest=_digest(raw),
                stream_id=loop.last_stream_id,
                bytes_handed_off=True,
            )
        if route == "chat.stream" and handed:
            error_code = (
                ErrorCode.PROTOCOL_MISMATCH.value
                if code == ErrorCode.PROTOCOL_MISMATCH
                else str(code)
            )
            attempt = self._stream_attempt(
                status=None,
                response_body=b"",
                stream_id=loop.last_stream_id,
                marker=marker,
                raw=raw,
                payload=payload,
                exchange=exchange,
                events=events,
                handed=True,
                error_code=error_code,
                stream_complete=False,
            )
            if code == ErrorCode.PROTOCOL_MISMATCH:
                return MuseProtocolStop(
                    request_marker=marker,
                    outbound_digest=_digest(raw),
                    stream_id=loop.last_stream_id,
                    bytes_handed_off=True,
                    attempt=attempt,
                )
            return attempt
        if route == "chat.stream":
            return self._never_sent(marker, raw)
        return _error(
            code if code == ErrorCode.PROTOCOL_MISMATCH else ErrorCode.NETWORK_TIMEOUT,
            "Muse read failed" if code != ErrorCode.PROTOCOL_MISMATCH else "Muse protocol failed",
            next_action=(
                "Stop. Do not retry this frame or weaken TLS."
                if code == ErrorCode.PROTOCOL_MISMATCH
                else "Retry the read. No mutation was handed off."
            ),
        )

    def _incomplete(
        self,
        route: str,
        exc: IncompleteBody,
        loop: ReceiveLoop,
        marker: str,
        raw: bytes,
        payload: Mapping[str, Any] | None,
        exchange: Exchange | None,
        events: list[dict[str, Any]] | None,
    ) -> GatewayResult | StreamAttempt | CoreError:
        return self._from_code(
            route,
            exc.detail.code,
            loop.handed_off,
            loop,
            marker,
            raw,
            payload,
            exchange,
            events,
        )

    def _after_uncertain_send(
        self,
        route: str,
        loop: ReceiveLoop,
        marker: str,
        raw: bytes,
        payload: Mapping[str, Any] | None,
        exchange: Exchange | None,
        events: list[dict[str, Any]] | None,
    ) -> StreamAttempt | MuseStateUnknown:
        if route != "chat.stream":
            return MuseStateUnknown(
                request_marker=marker,
                outbound_digest=_digest(raw),
                stream_id=loop.last_stream_id,
                bytes_handed_off=True,
            )
        return self._stream_attempt(
            status=None,
            response_body=b"",
            stream_id=loop.last_stream_id,
            marker=marker,
            raw=raw,
            payload=payload,
            exchange=exchange,
            events=events,
            handed=True,
            error_code=ErrorCode.EXTERNAL_STATE_UNKNOWN.value,
            stream_complete=False,
        )

    def _never_sent(self, marker: str, raw: bytes) -> StreamAttempt:
        evidence = AttemptEvidence(EVIDENCE_UNDECIDED, "request was not handed off")
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
        payload: Mapping[str, Any] | None,
        exchange: Exchange | None,
        events: list[dict[str, Any]] | None,
        handed: bool,
        error_code: str | None,
        stream_complete: bool = True,
        public_body: bytes | None = None,
    ) -> StreamAttempt:
        evidence = attempt_evidence(
            stream_complete=stream_complete,
            stream_status=status,
            body=response_body if stream_complete else None,
        )
        evidence = AttemptEvidence(evidence.verdict, self._secrets.text(evidence.detail))
        if public_body is None:
            public_body = self._secrets.body(response_body) if stream_complete else b""
        row = _exchange_for(payload, marker, exchange)
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
            body=public_body,
            error_code=error_code,
        )


IMPLEMENTED_TOOLS = frozenset(
    {
        "metamuse_muse_open",
        "metamuse_muse_send",
        "metamuse_muse_read",
        "metamuse_muse_close_exchange",
    }
)


def capability_status(ctx):
    from metamuse._muse_lifecycle import capability_status as status

    return status(ctx)


def register_operations(registry: dict[str, Operation] | None = None) -> None:
    from metamuse._muse_lifecycle import register_operations as register

    register(registry)


def open_muse_discussion(ctx: AppContext, request: m.MuseOpenRequest) -> m.Envelope:
    from metamuse._muse_lifecycle import open_muse_discussion as run

    return run(ctx, request)


def submit_muse_exchange(ctx: AppContext, request: m.MuseSendRequest) -> m.Envelope:
    from metamuse._muse_lifecycle import submit_muse_exchange as run

    return run(ctx, request)


def read_muse_exchange(ctx: AppContext, request: m.MuseReadRequest) -> m.Envelope:
    from metamuse._muse_lifecycle import read_muse_exchange as run

    return run(ctx, request)


def close_muse_exchange(ctx: AppContext, request: m.MuseCloseExchangeRequest) -> m.Envelope:
    from metamuse._muse_lifecycle import close_muse_exchange as run

    return run(ctx, request)
