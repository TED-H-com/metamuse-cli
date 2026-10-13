"""One receive consumer per link, with a deadline and a frame budget.

The fake gateway is in-process. Nothing here opens a socket.

Provenance: nikships/muse-cli (MIT, Copyright (c) 2026 Nik Ships), commit
ebcb6310432ad2d39927bcb59e686330b0538310, ``src/muse_cli/gateway.py``.
``_read_frame`` warns that two consumers corrupt Noise decrypt state and
misroute responses. ``Gateway.request`` stops reading at a deadline. This
file is new code: ownership lives on the link, ``collect`` refuses to run
without that ownership, and the deadline is ``time.monotonic``. The MIT
notice is in ``THIRD_PARTY_NOTICES.md``.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
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
DEFAULT_COLLECT_TIMEOUT_S = 30.0
MAX_COLLECT_FRAMES = 64


class InProcessLink:
    """Byte pipe. Receive ownership is on the link, not on one loop object."""

    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.incoming: deque[bytes | None] = deque()
        self._mu = threading.Lock()
        self._owner: object | None = None
        self._collecting = False

    def acquire(self, owner: object) -> None:
        with self._mu:
            if self._owner is not None:
                raise ProtocolMismatch("second receive consumer")
            self._owner = owner

    def release(self, owner: object) -> None:
        with self._mu:
            if self._owner is owner:
                self._owner = None
                self._collecting = False

    def begin_collect(self, owner: object) -> None:
        with self._mu:
            if self._owner is not owner:
                raise ProtocolMismatch("collect outside receive ownership")
            if self._collecting:
                raise ProtocolMismatch("concurrent collection")
            self._collecting = True

    def end_collect(self, owner: object) -> None:
        with self._mu:
            if self._owner is owner:
                self._collecting = False

    def send(self, frame: bytes) -> None:
        self.sent.append(frame)

    def push(self, frame: bytes | None) -> None:
        self.incoming.append(frame)

    def recv(self, timeout: float = 0.0) -> bytes | None:
        """Return the next frame, or None if none arrives within ``timeout``.

        This pipe does not block. An empty queue returns None immediately.
        A live socket must use ``timeout`` as its read deadline so a quiet
        peer cannot stall the single consumer past the collect deadline.
        """
        del timeout
        if not self.incoming:
            return None
        return self.incoming.popleft()


class ReceiveLoop:
    """Serial unary collector. The link allows one owner at a time."""

    def __init__(
        self,
        link: InProcessLink,
        *,
        max_frame_bytes: int = MAX_FRAME_BYTES,
        max_body_bytes: int = MAX_BODY_BYTES,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if max_frame_bytes > MAX_FRAME_BYTES:
            raise FrameTooLarge(f"plaintext frame cap {max_frame_bytes} exceeds {MAX_FRAME_BYTES}")
        self.link = link
        self.max_frame_bytes = max_frame_bytes
        self.max_body_bytes = max_body_bytes
        self._clock = clock or time.monotonic
        self._held = False
        self.next_stream_id = 1

    def __enter__(self) -> Self:
        self.link.acquire(self)
        self._held = True
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._held = False
        self.link.release(self)

    def open_request(
        self,
        *,
        verb: str,
        path: str,
        body: bytes = b"",
        headers: list[Header] | None = None,
        service: int = SERVICE_DAEMON,
    ) -> int:
        if not self._held:
            raise ProtocolMismatch("send outside receive ownership")
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

    def collect(
        self,
        stream_id: int,
        *,
        timeout_s: float = DEFAULT_COLLECT_TIMEOUT_S,
        max_frames: int = MAX_COLLECT_FRAMES,
    ) -> tuple[int, bytes]:
        """Return (status, body) only when end_body has been seen for this stream.

        ``begin_collect`` runs outside the ``try`` so a refused second caller
        does not clear the owner's collecting flag.
        """
        self.link.begin_collect(self)
        try:
            return self._collect(stream_id, timeout_s=timeout_s, max_frames=max_frames)
        finally:
            self.link.end_collect(self)

    def _collect(
        self,
        stream_id: int,
        *,
        timeout_s: float,
        max_frames: int,
    ) -> tuple[int, bytes]:
        status: int | None = None
        parts: list[bytes] = []
        total = 0
        complete = False
        skipped = 0
        seen = 0
        deadline = self._clock() + timeout_s
        while not complete:
            if seen >= max_frames:
                raise IncompleteBody("frame budget exhausted")
            if self._clock() >= deadline:
                raise IncompleteBody("receive deadline exhausted")
            remaining = deadline - self._clock()
            raw = self.link.recv(timeout=max(0.0, remaining))
            if raw is None:
                break
            seen += 1
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
