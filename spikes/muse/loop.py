"""One receive consumer, bounded frame and body sizes, no partial success.

The fake gateway is in-process. Nothing here opens a socket.
"""

from __future__ import annotations

from collections import deque
from typing import Self

from framing import (
    MAX_BODY_BYTES,
    MAX_FRAME_BYTES,
    MAX_SKIPPED_FRAMES,
    SERVICE_DAEMON,
    ApplicationRequest,
    ApplicationResponse,
    BodyChunk,
    FrameTooLarge,
    Header,
    IncompleteBody,
    ProtocolMismatch,
    ServiceFrame,
    decode_client_frame,
    decode_server_frame,
    encode_client_frame,
    encode_server_frame,
)

_CHUNK_ID = 1


class InProcessLink:
    """Byte pipe. `push` is what the fake gateway would send next."""

    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.incoming: deque[bytes | None] = deque()

    def send(self, frame: bytes) -> None:
        self.sent.append(frame)

    def push(self, frame: bytes | None) -> None:
        self.incoming.append(frame)

    def recv(self) -> bytes | None:
        if not self.incoming:
            return None
        return self.incoming.popleft()


class ReceiveLoop:
    """Serial unary collector. A second enter() while held is a protocol error."""

    def __init__(
        self,
        link: InProcessLink,
        *,
        max_frame_bytes: int = MAX_FRAME_BYTES,
        max_body_bytes: int = MAX_BODY_BYTES,
    ) -> None:
        self.link = link
        self.max_frame_bytes = max_frame_bytes
        self.max_body_bytes = max_body_bytes
        self._held = False
        self.next_stream_id = 1

    def __enter__(self) -> Self:
        if self._held:
            raise ProtocolMismatch("second receive consumer")
        self._held = True
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._held = False

    def open_request(
        self,
        *,
        verb: str,
        path: str,
        body: bytes = b"",
        headers: list[Header] | None = None,
        service: int = SERVICE_DAEMON,
    ) -> int:
        stream_id = self.next_stream_id
        self.next_stream_id += 1
        frame = ServiceFrame(
            stream_id=stream_id,
            kind="request",
            request=ApplicationRequest(
                verb=verb,
                path=path,
                headers=list(headers or []),
                body=body,
                end_body=True,
            ),
        )
        self.link.send(encode_client_frame(service, frame, _CHUNK_ID))
        return stream_id

    def collect(self, stream_id: int) -> tuple[int, bytes]:
        """Return (status, body) only when end_body has been seen for this stream."""
        status: int | None = None
        parts: list[bytes] = []
        total = 0
        complete = False
        skipped = 0
        while not complete:
            raw = self.link.recv()
            if raw is None:
                break
            if len(raw) > self.max_frame_bytes:
                raise FrameTooLarge(f"frame is {len(raw)} bytes")
            service_frame = decode_server_frame(raw, max_frame_bytes=self.max_frame_bytes)
            if service_frame.stream_id != stream_id:
                skipped += 1
                if skipped > MAX_SKIPPED_FRAMES:
                    raise ProtocolMismatch("too many frames for other streams")
                continue
            if service_frame.kind == "response":
                assert service_frame.response is not None
                status = service_frame.response.status
                total = _take(parts, total, service_frame.response.body, self.max_body_bytes)
                complete = service_frame.response.end_body
            elif service_frame.kind == "body_chunk":
                assert service_frame.body_chunk is not None
                total = _take(parts, total, service_frame.body_chunk.data, self.max_body_bytes)
                complete = service_frame.body_chunk.end_body
            elif service_frame.kind == "reset":
                reason = service_frame.reset.reason if service_frame.reset else ""
                raise ProtocolMismatch(f"stream reset {reason}")
            else:
                raise ProtocolMismatch(f"unknown frame type {service_frame.kind}")
        if not complete or status is None:
            raise IncompleteBody("response body is incomplete")
        return status, b"".join(parts)


def _take(parts: list[bytes], total: int, data: bytes, limit: int) -> int:
    total += len(data)
    if total > limit:
        raise FrameTooLarge(f"body is {total} bytes")
    if data:
        parts.append(data)
    return total


def response_frame(stream_id: int, status: int, body: bytes, *, end_body: bool) -> bytes:
    return encode_server_frame(
        ServiceFrame(
            stream_id=stream_id,
            kind="response",
            response=ApplicationResponse(status, [], body, end_body),
        )
    )


def chunk_frame(stream_id: int, data: bytes, *, end_body: bool) -> bytes:
    return encode_server_frame(
        ServiceFrame(
            stream_id=stream_id,
            kind="body_chunk",
            body_chunk=BodyChunk(data, end_body),
        )
    )


class FakeGateway:
    """Scripted in-process peer. It never dials a Muse host."""

    def __init__(self, link: InProcessLink, mode: str = "echo") -> None:
        self.link = link
        self.mode = mode
        self.seen_stream_ids: list[int] = []

    def pump_one(self) -> None:
        if not self.link.sent:
            raise ProtocolMismatch("client sent no frame")
        raw = self.link.sent[-1]
        _service, frame = decode_client_frame(raw)
        self.seen_stream_ids.append(frame.stream_id)
        for outgoing in scripted(self.mode, frame.stream_id):
            self.link.push(outgoing)


def scripted(mode: str, stream_id: int) -> list[bytes]:
    from framing import encode_unknown_kind_frame

    if mode == "echo":
        return [response_frame(stream_id, 200, b'{"ok":true}', end_body=True)]
    if mode == "decoy-then-match":
        return [
            response_frame(stream_id + 7, 200, b"other-stream", end_body=True),
            response_frame(stream_id, 200, b"matched", end_body=True),
        ]
    if mode == "incomplete":
        return [response_frame(stream_id, 200, b"partial", end_body=False)]
    if mode == "chunked":
        return [
            response_frame(stream_id, 200, b"AA", end_body=False),
            chunk_frame(stream_id, b"BB", end_body=False),
            chunk_frame(stream_id, b"CC", end_body=True),
        ]
    if mode == "unknown":
        return [encode_unknown_kind_frame(stream_id)]
    if mode == "oversize":
        return [b"x" * (MAX_FRAME_BYTES + 1)]
    raise ProtocolMismatch(f"unknown fake gateway mode {mode}")
