"""Noise_XX initiator for one Muse gateway connection.

Uses noiseprotocol (MIT, Copyright (c) 2017 Piotr Lizończyk). The pattern and
the empty handshake payloads are the ones documented for muse-cli. This file
does not copy that client's handshake. The library is imported inside
``load_noise`` so commands that never open a gateway do not import it.

Provenance: nikships/muse-cli (MIT, Copyright (c) 2026 Nik Ships), commit
ebcb6310432ad2d39927bcb59e686330b0538310, ``docs/PROTOCOL.md`` and
``src/muse_cli/gateway.py`` (initiator, empty payloads, pattern name).
The MIT notice is in ``THIRD_PARTY_NOTICES.md``.
"""

from __future__ import annotations

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
from metamuse.models import CoreError, ErrorCode

PATTERN = b"Noise_XX_25519_AESGCM_SHA256"
_NOISE_FAILURES = frozenset(
    {
        "NoiseInvalidMessage",
        "NoiseHandshakeError",
        "NoiseValueError",
        "NoiseMaxNonceError",
        "NoiseValidationError",
        "NoiseProtocolNameError",
        "InvalidTag",
    }
)


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
    """One Noise_XX role. Handshake payloads are empty. No socket."""

    def __init__(self, role: str) -> None:
        noise_connection, keypair, _invalid = load_noise()
        self.conn = noise_connection.from_name(PATTERN)
        if role == "initiator":
            self.conn.set_as_initiator()
        elif role == "responder":
            self.conn.set_as_responder()
        else:
            raise ProtocolMismatch("unknown noise role")
        self.conn.set_keypair_from_private_bytes(keypair.STATIC, os.urandom(32))
        self.conn.start_handshake()

    def _crypto(self, fn: Callable[[], bytes], message: str) -> bytes:
        try:
            return fn()
        except Exception as exc:
            if type(exc).__name__ not in _NOISE_FAILURES:
                raise
            failure = ProtocolMismatch(message)
        raise failure

    def write(self, payload: bytes = b"") -> bytes:
        if payload != b"":
            raise ProtocolMismatch("handshake payload must be empty")
        return self._crypto(
            lambda: bytes(self.conn.write_message(payload)),
            "noise handshake failed",
        )

    def read(self, message: bytes) -> bytes:
        return self._crypto(
            lambda: bytes(self.conn.read_message(message)),
            "handshake message was rejected",
        )

    def encrypt(self, plaintext: bytes) -> bytes:
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
        if len(ciphertext) > NOISE_MAX_CIPHERTEXT_BYTES:
            raise FrameTooLarge(f"ciphertext is {len(ciphertext)} bytes")
        return self._crypto(lambda: bytes(self.conn.decrypt(ciphertext)), "noise decrypt failed")

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
