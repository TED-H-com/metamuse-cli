"""Noise_XX initiator for one Muse gateway connection.

Uses noiseprotocol (MIT, Copyright (c) 2017 Piotr Lizończyk). The pattern and
legacy empty payloads are retained only in explicit offline helpers. The primary
standard path uses the independently documented challenge and strict bundle. This file
does not copy that client's handshake. The library is imported inside
``load_noise`` so commands that never open a gateway do not import it.

Provenance: nikships/muse-cli (MIT, Copyright (c) 2026 Nik Ships), commit
ebcb6310432ad2d39927bcb59e686330b0538310, ``docs/PROTOCOL.md`` and
``src/muse_cli/gateway.py`` (initiator, empty payloads, pattern name).
The MIT notice is in ``THIRD_PARTY_NOTICES.md``.
"""

from __future__ import annotations

import hmac
import math
import os
from collections.abc import Callable
from typing import Any

from metamuse._muse_codec import (
    MAX_FRAME_BYTES,
    NOISE_MAX_CIPHERTEXT_BYTES,
    FrameTooLarge,
    IncompleteBody,
    ProtocolMismatch,
)
from metamuse._muse_handshake_payloads import (
    HANDSHAKE_PAYLOAD_MAX_BYTES,
    ConfidentialChallenge,
    classify_payload,
    client_challenge,
    rv_shaped,
)
from metamuse.models import CoreError, ErrorCode

PATTERN = b"Noise_XX_25519_AESGCM_SHA256"


def load_noise() -> tuple[Any, Any, type[Exception]]:
    """Import noiseprotocol. Missing install is ``E_DEPENDENCY_MISSING``."""
    try:
        from noise.connection import Keypair, NoiseConnection
        from noise.exceptions import NoiseInvalidMessage
    except ImportError as exc:
        raise CoreError(
            ErrorCode.DEPENDENCY_MISSING,
            "noiseprotocol is not installed",
            next_action="Install the pinned noiseprotocol package. Do not hand-roll Noise.",
        ) from exc
    return NoiseConnection, Keypair, NoiseInvalidMessage


class NoiseParty:
    """One mature Noise_XX role, bounded payloads and conservative frame counters. No socket."""

    def __init__(self, role: str) -> None:
        noise_connection, keypair, _invalid = load_noise()
        if role not in ("initiator", "responder"):
            raise ProtocolMismatch("unknown noise role")

        def configure() -> bytes:
            self.conn = noise_connection.from_name(PATTERN)
            if role == "initiator":
                self.conn.set_as_initiator()
            else:
                self.conn.set_as_responder()
            self.conn.set_keypair_from_private_bytes(keypair.STATIC, os.urandom(32))
            self.conn.start_handshake()
            return b""

        self._crypto(configure, "noise initialization failed")
        self._sent_frames = 0
        self._received_frames = 0

    def _crypto(self, fn: Callable[[], bytes], message: str) -> bytes:
        try:
            return fn()
        except Exception as exc:
            if isinstance(exc, (AttributeError, TypeError)):
                failure = CoreError(
                    ErrorCode.DEPENDENCY_MISSING,
                    "Noise API is not the pinned shape",
                    next_action=(
                        "Install the pinned noiseprotocol package; do not bypass verification"
                    ),
                )
            else:
                failure = ProtocolMismatch(message)
        raise failure

    def write(self, payload: bytes = b"") -> bytes:
        if len(payload) > HANDSHAKE_PAYLOAD_MAX_BYTES:
            raise ProtocolMismatch("handshake payload exceeds cap")
        return self._crypto(
            lambda: bytes(self.conn.write_message(payload)),
            "noise handshake failed",
        )

    def read(self, message: bytes) -> bytes:
        if len(message) > NOISE_MAX_CIPHERTEXT_BYTES:
            raise ProtocolMismatch("handshake message exceeds cap")
        return self._crypto(
            lambda: bytes(self.conn.read_message(message)),
            "handshake message was rejected",
        )

    def encrypt(self, plaintext: bytes) -> bytes:
        if self._sent_frames >= 2**32:
            raise ProtocolMismatch("noise outbound frame budget exhausted")
        self._sent_frames += 1
        if len(plaintext) > MAX_FRAME_BYTES:
            raise FrameTooLarge(f"frame is {len(plaintext)} bytes")
        ciphertext = self._crypto(
            lambda: bytes(self.conn.encrypt(plaintext)),
            "noise encrypt failed",
        )
        if len(ciphertext) > NOISE_MAX_CIPHERTEXT_BYTES:
            raise FrameTooLarge(f"ciphertext is {len(ciphertext)} bytes")
        return ciphertext

    def decrypt(self, ciphertext: bytes) -> bytes:
        if self._received_frames >= 2**32:
            raise ProtocolMismatch("noise inbound frame budget exhausted")
        self._received_frames += 1
        if len(ciphertext) > NOISE_MAX_CIPHERTEXT_BYTES:
            raise FrameTooLarge(f"ciphertext is {len(ciphertext)} bytes")
        return self._crypto(lambda: bytes(self.conn.decrypt(ciphertext)), "noise decrypt failed")

    def responder_static(self) -> bytes:
        try:
            key = self.conn.noise_protocol.handshake_state.rs.public_bytes
            if not isinstance(key, bytes) or len(key) != 32:
                raise AttributeError
        except AttributeError:
            failure = CoreError(
                ErrorCode.DEPENDENCY_MISSING,
                "Noise responder-key API is unavailable",
                next_action=(
                    "Install the pinned noiseprotocol package; do not bypass verification"
                ),
            )
        else:
            failure = None
        if failure is not None:
            raise failure
        return key

    @property
    def finished(self) -> bool:
        return bool(self.conn.handshake_finished)


def handshake_initiator(
    party: NoiseParty,
    link: Any,
    *,
    timeout: float,
    clock: Callable[[], float],
) -> None:
    """Finish Noise_XX as the initiator. A non-empty peer payload stops the handshake."""
    deadline = clock() + timeout

    def recv() -> bytes:
        if clock() >= deadline:
            raise IncompleteBody("receive deadline exhausted")
        data = link.recv(max(0.0, deadline - clock()))
        if clock() >= deadline:
            raise IncompleteBody("receive deadline exhausted")
        if not data:
            raise IncompleteBody("receive deadline exhausted")
        return data

    link.send(party.write(b""))
    reply = party.read(recv())
    if reply != b"":
        raise ProtocolMismatch("attested or non-empty handshake payload")
    link.send(party.write(b""))
    if not party.finished:
        raise ProtocolMismatch("handshake did not finish")


def handshake_standard(
    party: NoiseParty,
    link: Any,
    *,
    timeout: float,
    clock: Callable[[], float],
    confidential: Callable[[], None],
    standard_handoff: Callable[[Callable[[], None]], None],
    remember_nonce: Callable[[bytes], None] | None = None,
) -> None:
    """Strict standard subset: fresh msg1, classified/equal msg2, committed context, empty msg3."""
    deadline = clock() + timeout
    if not math.isfinite(timeout) or timeout <= 0 or clock() >= deadline:
        raise IncompleteBody("handshake deadline exhausted")
    nonce = os.urandom(32)
    if remember_nonce is not None:
        remember_nonce(nonce)
    message = party.write(client_challenge(nonce))
    if clock() >= deadline:
        raise IncompleteBody("handshake deadline exhausted")
    link.send(message)
    if clock() >= deadline:
        raise IncompleteBody("receive deadline exhausted")
    data = link.recv(max(0.0, deadline - clock()))
    if not data or clock() >= deadline:
        raise IncompleteBody("receive deadline exhausted")
    reply = party.read(data)
    if rv_shaped(reply):
        confidential()
        raise ProtocolMismatch("confidential handshake requires a supported verifier")
    result = classify_payload(reply)
    if isinstance(result, ConfidentialChallenge):
        confidential()
        raise ProtocolMismatch("confidential handshake requires a supported verifier")
    if not hmac.compare_digest(result.key, party.responder_static()) or not hmac.compare_digest(
        result.nonce, nonce
    ):
        raise ProtocolMismatch("standard key or nonce did not match")

    def handoff():
        if clock() >= deadline:
            raise IncompleteBody("handshake deadline exhausted")
        link.send(party.write(b""))
        if not party.finished:
            raise ProtocolMismatch("handshake did not finish")

    standard_handoff(handoff)


def finished_pair() -> tuple[NoiseParty, NoiseParty]:
    """In-memory initiator and responder with empty payloads. No socket."""
    initiator = NoiseParty("initiator")
    responder = NoiseParty("responder")
    message1 = initiator.write(b"")
    if responder.read(message1) != b"":
        raise ProtocolMismatch("handshake payload was not empty")
    message2 = responder.write(b"")
    if initiator.read(message2) != b"":
        raise ProtocolMismatch("handshake payload was not empty")
    message3 = initiator.write(b"")
    if responder.read(message3) != b"":
        raise ProtocolMismatch("handshake payload was not empty")
    if not (initiator.finished and responder.finished):
        raise ProtocolMismatch("handshake did not finish")
    return initiator, responder
