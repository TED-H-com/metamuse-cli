"""Gateway frame codec for the Muse transport.

The reviewed spike in ``spikes/muse/framing.py`` is the historical record.
This module is that codec moved onto the production error codes.

Wire layout restated from the descriptor binaries shipped in
nikships/muse-cli (MIT, Copyright (c) 2026 Nik Ships), commit
ebcb6310432ad2d39927bcb59e686330b0538310:

- src/muse_cli/desc0.bin sha256 5479a20cd5a9bf817c2a7ba4eec56b354d8576f565d9096fd2a3a8223e820d00
  package ingress_rev_proxy, message NoiseTransportFrame
- src/muse_cli/desc1.bin sha256 5ea3f209386a4d47d28ee06473a2defad35536725bf2d3d847d31b04ec1a8868
  package hatch.noise, messages ServiceRequest, ServiceResponse, ServiceFrame,
  ApplicationRequest, ApplicationResponse, BodyChunk, Reset, Header

This module is a new protobuf wire encoder and decoder for those field
numbers. It is not a copy of muse_cli/gateway.py, and it does not embed the
descriptor binaries. Field numbers are an inspection result, not something
PROTOCOL.md spells out. See docs/dev/MUSE_SPIKE.md.

Plaintext frames are capped at the noiseprotocol 0.3.1 transport budget.
That library's ``noise.constants.MAX_MESSAGE_LEN`` is 65535 and applies to
the ciphertext (``noise/connection.py`` encrypt and decrypt). The AES-GCM
tag is 16 bytes, so the largest plaintext that still round-trips is 65519.
``encrypt`` itself accepts a larger plaintext; the peer then rejects the
ciphertext. This cap refuses that plaintext before it is handed to Noise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from metamuse.models import CoreError, ErrorCode

# noiseprotocol 0.3.1: noise/constants.py MAX_MESSAGE_LEN, plus the 16-byte
# Noise AEAD tag. Ciphertext budget and plaintext budget are different.
NOISE_MAX_CIPHERTEXT_BYTES = 65535
NOISE_TAG_BYTES = 16
NOISE_MAX_PLAINTEXT_BYTES = NOISE_MAX_CIPHERTEXT_BYTES - NOISE_TAG_BYTES

MAX_FRAME_BYTES = NOISE_MAX_PLAINTEXT_BYTES
MAX_BODY_BYTES = 1024 * 1024
MAX_SKIPPED_FRAMES = 32

SERVICE_DAEMON = 0
SERVICE_SENTINEL = 1
SERVICE_VAULT = 2
SERVICE_AUTHD = 3

RESET_UNSPECIFIED = 0
RESET_CANCELLED = 1
RESET_TIMEOUT = 2
RESET_PROTOCOL_ERROR = 3
RESET_REFUSED_STREAM = 4
RESET_INTERNAL = 5
RESET_UNAVAILABLE = 6

_PAIR = re.compile(r"(?i)\b(auth_token|access_token|hatch_sess)=([^&\s\"']+)")
_BEARER = re.compile(r"(?i)((?:authorization:\s*)?bearer\s+)\S+")
_COOKIE = re.compile(r"(?i)cookie:\s*[^\r\n]+")


def json_depth(body: bytes, *, limit: int = 32) -> int:
    """Brace depth of a JSON text. Stops once ``limit`` is exceeded."""
    depth = 0
    maximum = 0
    in_string = False
    escape = False
    for byte in body:
        if in_string:
            if escape:
                escape = False
            elif byte == 0x5C:
                escape = True
            elif byte == 0x22:
                in_string = False
            continue
        if byte == 0x22:
            in_string = True
        elif byte in (0x7B, 0x5B):
            depth += 1
            if depth > maximum:
                maximum = depth
            if maximum > limit:
                return maximum
        elif byte in (0x7D, 0x5D) and depth:
            depth -= 1
    return maximum


def redact(text: str) -> str:
    """Strip credential-shaped substrings before they reach an error or a log."""
    from metamuse._muse_redact import sanitize_text

    text = _COOKIE.sub("cookie: [redacted]", text)
    text = _BEARER.sub(lambda match: match.group(1) + "[redacted]", text)
    return sanitize_text(_PAIR.sub(lambda match: match.group(1) + "=[redacted]", text))


class ProtocolMismatch(CoreError):
    """Unknown field, multi-chunk frame, or oversize frame. Never a successful response."""

    def __init__(self, message: str):
        super().__init__(
            ErrorCode.PROTOCOL_MISMATCH,
            redact(message),
            next_action="Stop. Do not retry this frame or weaken TLS.",
        )


class FrameTooLarge(ProtocolMismatch):
    """Oversize plaintext, ciphertext, or body. The public code stays E_PROTOCOL_MISMATCH."""


class IncompleteBody(CoreError):
    """Reassembly did not see end_body. The bytes collected so far are not a result."""

    def __init__(self, message: str, *, code: ErrorCode = ErrorCode.NETWORK_TIMEOUT):
        action = (
            "Shrink the collect window."
            if code == ErrorCode.LIMIT_REACHED
            else "Reconcile the original session before any resend."
        )
        super().__init__(code, redact(message), next_action=action)


@dataclass
class Header:
    key: str
    value: str = field(repr=False)


@dataclass
class ApplicationRequest:
    verb: str
    path: str = field(repr=False)
    headers: list[Header] = field(default_factory=list, repr=False)
    body: bytes = field(default=b"", repr=False)
    end_body: bool = True


@dataclass
class ApplicationResponse:
    status: int
    headers: list[Header] = field(default_factory=list, repr=False)
    body: bytes = field(default=b"", repr=False)
    end_body: bool = False


@dataclass
class BodyChunk:
    data: bytes = field(default=b"", repr=False)
    end_body: bool = False


@dataclass
class Reset:
    code: int = 0
    reason: str = field(default="", repr=False)


@dataclass
class ServiceFrame:
    stream_id: int
    kind: str
    request: ApplicationRequest | None = None
    response: ApplicationResponse | None = None
    body_chunk: BodyChunk | None = None
    reset: Reset | None = None


@dataclass
class NoiseTransportFrame:
    chunk_id: int
    chunk_index: int
    total_chunks: int
    payload: bytes = field(repr=False)


_INT64_MAX = (1 << 63) - 1
_INT32_MIN = -(1 << 31)
_INT32_MAX = (1 << 31) - 1


def _encode_varint(value: int) -> bytes:
    if value < 0:
        value += 1 << 64
    if value < 0 or value >= 1 << 64:
        raise ProtocolMismatch("varint out of uint64 range")
    out = bytearray()
    while True:
        piece = value & 0x7F
        value >>= 7
        if value:
            out.append(piece | 0x80)
        else:
            out.append(piece)
            return bytes(out)


def _decode_varint(buf: bytes, index: int) -> tuple[int, int]:
    """Decode one uint64 varint. The tenth byte may only carry bit 63."""
    value = 0
    shift = 0
    for _ in range(10):
        if index >= len(buf):
            raise ProtocolMismatch("truncated varint")
        byte = buf[index]
        index += 1
        if shift == 63 and byte > 1:
            raise ProtocolMismatch("varint out of range")
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            if (shift > 0 and byte == 0) or value >= 1 << 64:
                raise ProtocolMismatch("varint out of range")
            return value, index
        shift += 7
    raise ProtocolMismatch("varint too long")


def _as_int64(value: int) -> int:
    if value < 0 or value >= 1 << 64:
        raise ProtocolMismatch("integer out of int64 range")
    if value >= 1 << 63:
        return value - (1 << 64)
    return value


def _as_int32(value: int) -> int:
    signed = _as_int64(value)
    if signed < _INT32_MIN or signed > _INT32_MAX:
        raise ProtocolMismatch("status out of int32 range")
    return signed


def _utf8(payload: bytes) -> str:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is None:
        raise ProtocolMismatch("frame text is not utf-8")
    return text


def _key(field_number: int, wire_type: int) -> bytes:
    return _encode_varint((field_number << 3) | wire_type)


def _varint_field(field_number: int, value: int) -> bytes:
    return _key(field_number, 0) + _encode_varint(value)


def _bytes_field(field_number: int, payload: bytes) -> bytes:
    return _key(field_number, 2) + _encode_varint(len(payload)) + payload


def _string_field(field_number: int, text: str) -> bytes:
    return _bytes_field(field_number, text.encode("utf-8"))


def _iter_fields(buf: bytes):
    index = 0
    while index < len(buf):
        key, index = _decode_varint(buf, index)
        field_number, wire_type = key >> 3, key & 7
        if field_number <= 0:
            raise ProtocolMismatch("invalid protobuf field number")
        if wire_type == 0:
            value, index = _decode_varint(buf, index)
            yield field_number, wire_type, value
        elif wire_type == 2:
            length, index = _decode_varint(buf, index)
            end = index + length
            if length < 0 or end > len(buf):
                raise ProtocolMismatch("truncated length-delimited field")
            yield field_number, wire_type, buf[index:end]
            index = end
        else:
            raise ProtocolMismatch(f"unsupported protobuf wire type {wire_type}")


def _encode_header(header: Header) -> bytes:
    return _string_field(1, header.key) + _string_field(2, header.value)


def _decode_header(buf: bytes) -> Header:
    key = ""
    value = ""
    for field_number, wire_type, payload in _iter_fields(buf):
        if wire_type != 2 or field_number not in (1, 2):
            raise ProtocolMismatch("malformed header")
        text = _utf8(payload)
        if field_number == 1:
            key = text
        else:
            value = text
    return Header(key, value)


def encode_application_request(request: ApplicationRequest) -> bytes:
    # Plain proto3 fields: the official encoder omits default 0 / false / empty.
    parts = []
    if request.verb:
        parts.append(_string_field(1, request.verb))
    if request.path:
        parts.append(_string_field(2, request.path))
    for header in request.headers:
        parts.append(_bytes_field(3, _encode_header(header)))
    if request.body:
        parts.append(_bytes_field(4, request.body))
    if request.end_body:
        parts.append(_varint_field(5, 1))
    return b"".join(parts)


def _decode_application_request(buf: bytes) -> ApplicationRequest:
    verb = ""
    path = ""
    headers: list[Header] = []
    body = b""
    end_body = False
    seen_end = False
    for field_number, wire_type, payload in _iter_fields(buf):
        if field_number == 1 and wire_type == 2:
            verb = _utf8(payload)
        elif field_number == 2 and wire_type == 2:
            path = _utf8(payload)
        elif field_number == 3 and wire_type == 2:
            headers.append(_decode_header(payload))
        elif field_number == 4 and wire_type == 2:
            body = payload
        elif field_number == 5 and wire_type == 0:
            end_body = bool(payload)
            seen_end = True
        else:
            raise ProtocolMismatch("malformed application request")
    if not seen_end:
        end_body = False
    return ApplicationRequest(verb, path, headers, body, end_body)


def encode_application_response(response: ApplicationResponse) -> bytes:
    parts = []
    if response.status:
        parts.append(_varint_field(1, response.status))
    for header in response.headers:
        parts.append(_bytes_field(2, _encode_header(header)))
    if response.body:
        parts.append(_bytes_field(3, response.body))
    if response.end_body:
        parts.append(_varint_field(4, 1))
    return b"".join(parts)


def _decode_application_response(buf: bytes) -> ApplicationResponse:
    status = None
    headers: list[Header] = []
    body = b""
    end_body = False
    for field_number, wire_type, payload in _iter_fields(buf):
        if field_number == 1 and wire_type == 0:
            status = _as_int32(payload)
        elif field_number == 2 and wire_type == 2:
            headers.append(_decode_header(payload))
        elif field_number == 3 and wire_type == 2:
            body = payload
        elif field_number == 4 and wire_type == 0:
            end_body = bool(payload)
        else:
            raise ProtocolMismatch("malformed application response")
    if status is None:
        raise ProtocolMismatch("application response missing status")
    return ApplicationResponse(status, headers, body, end_body)


def encode_body_chunk(chunk: BodyChunk) -> bytes:
    parts = []
    if chunk.data:
        parts.append(_bytes_field(1, chunk.data))
    if chunk.end_body:
        parts.append(_varint_field(2, 1))
    return b"".join(parts)


def _decode_body_chunk(buf: bytes) -> BodyChunk:
    data = b""
    end_body = False
    for field_number, wire_type, payload in _iter_fields(buf):
        if field_number == 1 and wire_type == 2:
            data = payload
        elif field_number == 2 and wire_type == 0:
            end_body = bool(payload)
        else:
            raise ProtocolMismatch("malformed body chunk")
    return BodyChunk(data, end_body)


def encode_reset(reset: Reset) -> bytes:
    parts = []
    if reset.code:
        parts.append(_varint_field(1, reset.code))
    if reset.reason:
        parts.append(_string_field(2, reset.reason))
    return b"".join(parts)


def _decode_reset(buf: bytes) -> Reset:
    code = 0
    reason = ""
    for field_number, wire_type, payload in _iter_fields(buf):
        if field_number == 1 and wire_type == 0:
            code = payload
        elif field_number == 2 and wire_type == 2:
            reason = _utf8(payload)
        else:
            raise ProtocolMismatch("malformed reset")
    return Reset(code, reason)


def encode_service_frame(frame: ServiceFrame) -> bytes:
    parts = []
    if frame.stream_id:
        if type(frame.stream_id) is not int or not 1 <= frame.stream_id <= _INT64_MAX:
            raise ProtocolMismatch("stream id out of positive int64 range")
        parts.append(_varint_field(1, frame.stream_id))
    if frame.kind == "request":
        if frame.request is None:
            raise ProtocolMismatch("request frame missing body")
        parts.append(_bytes_field(2, encode_application_request(frame.request)))
    elif frame.kind == "response":
        if frame.response is None:
            raise ProtocolMismatch("response frame missing body")
        parts.append(_bytes_field(3, encode_application_response(frame.response)))
    elif frame.kind == "body_chunk":
        if frame.body_chunk is None:
            raise ProtocolMismatch("body_chunk frame missing body")
        parts.append(_bytes_field(4, encode_body_chunk(frame.body_chunk)))
    elif frame.kind == "reset":
        if frame.reset is None:
            raise ProtocolMismatch("reset frame missing body")
        parts.append(_bytes_field(5, encode_reset(frame.reset)))
    else:
        raise ProtocolMismatch(f"unknown frame type {frame.kind}")
    return b"".join(parts)


def decode_service_frame(buf: bytes) -> ServiceFrame:
    stream_id = None
    kind = None
    request = None
    response = None
    body_chunk = None
    reset = None
    for field_number, wire_type, payload in _iter_fields(buf):
        if field_number == 1 and wire_type == 0:
            stream_id = _as_int64(payload)
            if not 1 <= stream_id <= _INT64_MAX:
                raise ProtocolMismatch("stream id out of positive int64 range")
        elif field_number == 2 and wire_type == 2:
            kind = _one_kind(kind, "request")
            request = _decode_application_request(payload)
        elif field_number == 3 and wire_type == 2:
            kind = _one_kind(kind, "response")
            response = _decode_application_response(payload)
        elif field_number == 4 and wire_type == 2:
            kind = _one_kind(kind, "body_chunk")
            body_chunk = _decode_body_chunk(payload)
        elif field_number == 5 and wire_type == 2:
            kind = _one_kind(kind, "reset")
            reset = _decode_reset(payload)
        else:
            raise ProtocolMismatch(f"unknown frame type field {field_number}")
    if stream_id is None or kind is None:
        raise ProtocolMismatch("service frame missing stream id or kind")
    return ServiceFrame(stream_id, kind, request, response, body_chunk, reset)


def _one_kind(current: str | None, new: str) -> str:
    if current is not None:
        raise ProtocolMismatch("service frame has more than one kind")
    return new


def encode_service_request(service: int, frame: ServiceFrame) -> bytes:
    parts = []
    # service 0 is SERVICE_DAEMON and is omitted on the wire (plain proto3 enum).
    if service:
        parts.append(_varint_field(1, service))
    parts.append(_bytes_field(2, encode_service_frame(frame)))
    return b"".join(parts)


def encode_service_response(frame: ServiceFrame) -> bytes:
    return _bytes_field(1, encode_service_frame(frame))


def decode_service_response(buf: bytes) -> ServiceFrame:
    payload = None
    for field_number, wire_type, value in _iter_fields(buf):
        if field_number == 1 and wire_type == 2:
            payload = value
        else:
            raise ProtocolMismatch(f"unknown service response field {field_number}")
    if payload is None:
        raise ProtocolMismatch("service response missing payload")
    return decode_service_frame(payload)


def decode_service_request(buf: bytes) -> tuple[int, ServiceFrame]:
    service = 0
    inner = None
    for field_number, wire_type, value in _iter_fields(buf):
        if field_number == 1 and wire_type == 0:
            service = value
        elif field_number == 2 and wire_type == 2:
            inner = value
        else:
            raise ProtocolMismatch(f"unknown service request field {field_number}")
    if inner is None:
        raise ProtocolMismatch("service request is incomplete")
    return service, decode_service_frame(inner)


def decode_client_frame(
    raw: bytes, *, max_frame_bytes: int = MAX_FRAME_BYTES
) -> tuple[int, ServiceFrame]:
    if len(raw) > max_frame_bytes:
        raise FrameTooLarge(f"frame is {len(raw)} bytes")
    outer = decode_noise_transport_frame(raw)
    return decode_service_request(outer.payload)


def encode_noise_transport_frame(frame: NoiseTransportFrame) -> bytes:
    if frame.total_chunks != 1 or frame.chunk_index != 0:
        raise ProtocolMismatch("multi-chunk reassembly is not supported")
    raw = b"".join(
        [
            _varint_field(1, frame.chunk_id),
            _varint_field(2, frame.chunk_index),
            _varint_field(3, frame.total_chunks),
            _bytes_field(4, frame.payload),
        ]
    )
    if len(raw) > MAX_FRAME_BYTES:
        raise FrameTooLarge(f"frame is {len(raw)} bytes")
    return raw


def decode_noise_transport_frame(buf: bytes) -> NoiseTransportFrame:
    chunk_id = None
    chunk_index = None
    total_chunks = None
    payload = None
    for field_number, wire_type, value in _iter_fields(buf):
        if field_number == 1 and wire_type == 0:
            chunk_id = _as_int64(value)
        elif field_number == 2 and wire_type == 0:
            chunk_index = value
        elif field_number == 3 and wire_type == 0:
            total_chunks = value
        elif field_number == 4 and wire_type == 2:
            payload = value
        else:
            raise ProtocolMismatch(f"unknown noise transport field {field_number}")
    if None in (chunk_id, chunk_index, total_chunks, payload):
        raise ProtocolMismatch("noise transport frame is incomplete")
    if total_chunks != 1 or chunk_index != 0:
        raise ProtocolMismatch("unsupported multi-chunk frame; partial reassembly is not success")
    return NoiseTransportFrame(chunk_id, chunk_index, total_chunks, payload)


def encode_client_frame(service: int, frame: ServiceFrame, chunk_id: int) -> bytes:
    outer = encode_noise_transport_frame(
        NoiseTransportFrame(
            chunk_id=chunk_id,
            chunk_index=0,
            total_chunks=1,
            payload=encode_service_request(service, frame),
        )
    )
    if len(outer) > MAX_FRAME_BYTES:
        raise FrameTooLarge(f"frame is {len(outer)} bytes")
    return outer


def encode_server_frame(frame: ServiceFrame, chunk_id: int = 1) -> bytes:
    outer = encode_noise_transport_frame(
        NoiseTransportFrame(
            chunk_id=chunk_id,
            chunk_index=0,
            total_chunks=1,
            payload=encode_service_response(frame),
        )
    )
    if len(outer) > MAX_FRAME_BYTES:
        raise FrameTooLarge(f"frame is {len(outer)} bytes")
    return outer


def decode_server_frame(raw: bytes, *, max_frame_bytes: int = MAX_FRAME_BYTES) -> ServiceFrame:
    if len(raw) > max_frame_bytes:
        raise FrameTooLarge(f"frame is {len(raw)} bytes")
    outer = decode_noise_transport_frame(raw)
    return decode_service_response(outer.payload)


def encode_unknown_kind_frame(stream_id: int) -> bytes:
    """Server frame whose ServiceFrame uses field 6, which is not a known kind."""
    unknown = _varint_field(1, stream_id) + _bytes_field(6, b"\x08\x01")
    response = _bytes_field(1, unknown)
    return encode_noise_transport_frame(
        NoiseTransportFrame(chunk_id=1, chunk_index=0, total_chunks=1, payload=response)
    )
