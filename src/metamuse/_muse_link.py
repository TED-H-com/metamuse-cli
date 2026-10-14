"""One receive consumer for a Muse gateway connection.

Provenance: nikships/muse-cli (MIT, Copyright (c) 2026 Nik Ships), commit
ebcb6310432ad2d39927bcb59e686330b0538310, ``src/muse_cli/gateway.py``.
``_read_frame`` warns that two consumers corrupt Noise decrypt state.
``Gateway.request`` stops reading at a deadline. This file is new code:
ownership lives on the link, ``collect`` refuses to run without that
ownership, and the deadline is an injected monotonic clock. The MIT notice
is in ``THIRD_PARTY_NOTICES.md``.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Protocol, Self

from metamuse._muse_codec import (
    MAX_BODY_BYTES,
    MAX_FRAME_BYTES,
    MAX_SKIPPED_FRAMES,
    NOISE_MAX_CIPHERTEXT_BYTES,
    SERVICE_DAEMON,
    ApplicationRequest,
    BodyChunk,
    FrameTooLarge,
    Header,
    IncompleteBody,
    ProtocolMismatch,
    ServiceFrame,
    decode_server_frame,
    encode_client_frame,
)
from metamuse._muse_noise import NoiseParty
from metamuse.models import ErrorCode

_CHUNK_ID = 1
DEFAULT_COLLECT_TIMEOUT_S = 30.0
MAX_COLLECT_FRAMES = 64


class ByteLink(Protocol):
    """One ordered byte pipe. Implementations must honor ``timeout`` on recv."""

    def send(self, data: bytes) -> None: ...

    def recv(self, timeout: float) -> bytes | None: ...

    def close(self) -> None: ...


class SendUncertain(Exception):
    """The pipe may have accepted some of the frame. Delivery is unknown."""


class OwnedLink:
    """Single-consumer ownership around a byte pipe that does not know about it."""

    def __init__(self, raw: ByteLink) -> None:
        self.raw = raw
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

    def send(self, data: bytes) -> None:
        self.raw.send(data)

    def recv(self, timeout: float) -> bytes | None:
        return self.raw.recv(timeout)

    def close(self) -> None:
        self.raw.close()


class ScriptedLink:
    """In-memory frames for tests. ``recv`` does not block and opens no socket.

    A live socket must use ``timeout`` as its read deadline so a quiet peer
    cannot stall the single consumer past the collect deadline.
    """

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
        del timeout
        if not self.incoming:
            return None
        return self.incoming.popleft()

    def close(self) -> None:
        return None


class ReceiveLoop:
    """Serial unary collector. The link allows one owner at a time."""

    def __init__(
        self,
        link: ScriptedLink | OwnedLink,
        *,
        noise: NoiseParty | None = None,
        max_frame_bytes: int = MAX_FRAME_BYTES,
        max_body_bytes: int = MAX_BODY_BYTES,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if max_frame_bytes > MAX_FRAME_BYTES:
            raise FrameTooLarge(f"plaintext frame cap {max_frame_bytes} exceeds {MAX_FRAME_BYTES}")
        self.link = link
        self.noise = noise
        self.max_frame_bytes = max_frame_bytes
        self.max_body_bytes = max_body_bytes
        self._clock = clock or time.monotonic
        self._held = False
        self.next_stream_id = 1
        self.last_stream_id: int | None = None
        self.handed_off = False

    def __enter__(self) -> Self:
        self.link.acquire(self)
        self._held = True
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
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
        self.last_stream_id = stream_id
        # Reset before encoding so a failure prior to send is "never handed off".
        # SendUncertain means the pipe may already have accepted bytes.
        self.handed_off = False
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
        plaintext = encode_client_frame(service, frame, _CHUNK_ID)
        wire = self.noise.encrypt(plaintext) if self.noise is not None else plaintext
        try:
            self.link.send(wire)
        except SendUncertain:
            self.handed_off = True
            raise
        self.handed_off = True
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
        wire_limit = NOISE_MAX_CIPHERTEXT_BYTES if self.noise is not None else self.max_frame_bytes
        while not complete:
            if seen >= max_frames:
                raise IncompleteBody("frame budget exhausted", code=ErrorCode.LIMIT_REACHED)
            if self._clock() >= deadline:
                raise IncompleteBody("receive deadline exhausted")
            remaining = deadline - self._clock()
            raw = self.link.recv(timeout=max(0.0, remaining))
            if raw is None:
                break
            seen += 1
            if len(raw) > wire_limit:
                raise FrameTooLarge(f"frame is {len(raw)} bytes")
            plaintext = self.noise.decrypt(raw) if self.noise is not None else raw
            service_frame = decode_server_frame(plaintext, max_frame_bytes=self.max_frame_bytes)
            if service_frame.stream_id != stream_id:
                skipped += 1
                if skipped > MAX_SKIPPED_FRAMES:
                    raise ProtocolMismatch("too many frames for other streams")
                continue
            if service_frame.kind == "response":
                if service_frame.response is None:
                    raise ProtocolMismatch("response frame missing body")
                status = service_frame.response.status
                total = _take(parts, total, service_frame.response.body, self.max_body_bytes)
                complete = service_frame.response.end_body
            elif service_frame.kind == "body_chunk":
                if service_frame.body_chunk is None:
                    raise ProtocolMismatch("body_chunk frame missing body")
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
    from metamuse._muse_codec import ApplicationResponse, encode_server_frame

    return encode_server_frame(
        ServiceFrame(
            stream_id=stream_id,
            kind="response",
            response=ApplicationResponse(status, [], body, end_body),
        )
    )


def chunk_frame(stream_id: int, data: bytes, *, end_body: bool) -> bytes:
    return encode_server_frame_chunk(stream_id, data, end_body=end_body)


def encode_server_frame_chunk(stream_id: int, data: bytes, *, end_body: bool) -> bytes:
    from metamuse._muse_codec import encode_server_frame

    return encode_server_frame(
        ServiceFrame(
            stream_id=stream_id,
            kind="body_chunk",
            body_chunk=BodyChunk(data, end_body),
        )
    )
