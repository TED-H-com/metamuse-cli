# /// script
# requires-python = ">=3.12"
# dependencies = ["noiseprotocol==0.3.1"]
# ///
"""In-process Noise_XX handshake against a fake gateway.

Uses the noiseprotocol library (the same pattern name muse-cli selects).
Payloads on all three handshake messages are empty, matching docs/PROTOCOL.md.
No socket, no token, no Muse host.

Run: uv run --python 3.12 spikes/muse/handshake.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from framing import (
    ApplicationRequest,
    ServiceFrame,
    decode_client_frame,
    decode_server_frame,
    encode_client_frame,
)
from loop import response_frame
from noise.exceptions import NoiseInvalidMessage

PATTERN = b"Noise_XX_25519_AESGCM_SHA256"


def _party(role: str):
    from noise.connection import Keypair, NoiseConnection

    party = NoiseConnection.from_name(PATTERN)
    if role == "initiator":
        party.set_as_initiator()
    elif role == "responder":
        party.set_as_responder()
    else:
        raise RuntimeError(role)
    party.set_keypair_from_private_bytes(Keypair.STATIC, os.urandom(32))
    party.start_handshake()
    return party


def handshake() -> dict:
    """Initiator is the client. Responder is the fake gateway. Bytes stay in memory."""
    client = _party("initiator")
    gateway = _party("responder")

    message1 = bytes(client.write_message(b""))
    if gateway.read_message(message1) != b"":
        raise RuntimeError("handshake message 1 payload was not empty")
    message2 = bytes(gateway.write_message(b""))
    if client.read_message(message2) != b"":
        raise RuntimeError("handshake message 2 payload was not empty")
    message3 = bytes(client.write_message(b""))
    if gateway.read_message(message3) != b"":
        raise RuntimeError("handshake message 3 payload was not empty")
    if not (client.handshake_finished and gateway.handshake_finished):
        raise RuntimeError("handshake did not finish")

    request = ServiceFrame(
        stream_id=1,
        kind="request",
        request=ApplicationRequest(verb="POST", path="/chat/stream", body=b"{}", end_body=True),
    )
    plaintext = encode_client_frame(0, request, chunk_id=7)
    ciphertext = bytes(client.encrypt(plaintext))
    opened = bytes(gateway.decrypt(ciphertext))
    service, decoded = decode_client_frame(opened)
    if service != 0 or decoded.stream_id != 1 or decoded.kind != "request":
        raise RuntimeError("decrypted request did not match")

    reply = response_frame(1, 200, b'{"ok":true}', end_body=True)
    reply_cipher = bytes(gateway.encrypt(reply))
    reply_plain = bytes(client.decrypt(reply_cipher))
    response = decode_server_frame(reply_plain)
    if response.stream_id != 1 or response.response is None or not response.response.end_body:
        raise RuntimeError("decrypted response was not a finished stream 1 body")
    if response.response.body != b'{"ok":true}':
        raise RuntimeError("response body mismatch")

    # A second finished handshake is a different session. Its transport keys
    # must not open the first session's ciphertext. NoiseInvalidMessage is the
    # library's AES-GCM authentication failure.
    other_client = _party("initiator")
    other_gateway = _party("responder")
    other1 = bytes(other_client.write_message(b""))
    if other_gateway.read_message(other1) != b"":
        raise RuntimeError("second handshake payload was not empty")
    other2 = bytes(other_gateway.write_message(b""))
    if other_client.read_message(other2) != b"":
        raise RuntimeError("second handshake payload was not empty")
    other3 = bytes(other_client.write_message(b""))
    if other_gateway.read_message(other3) != b"":
        raise RuntimeError("second handshake payload was not empty")
    try:
        other_gateway.decrypt(ciphertext)
    except NoiseInvalidMessage:
        rejected = True
    else:
        rejected = False
    if not rejected:
        raise RuntimeError("another session decrypted this session's frame")

    return {
        "pattern": PATTERN.decode("ascii"),
        "handshake_finished": True,
        "empty_payloads": True,
        "roundtrip": "ok",
        "stream_id": response.stream_id,
        "library": "noiseprotocol",
        "other_session_rejected": True,
    }


def main() -> None:
    json.dump(handshake(), sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
