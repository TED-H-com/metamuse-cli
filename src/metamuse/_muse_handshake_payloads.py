"""Independent bounded codecs for the documented primary Muse handshake subset.

Interoperability facts: docs/dev/MUSE_SPIKE.md §9 (2026-10-14). No vendor
schema, descriptor, parser or cryptographic implementation is incorporated.
"""

from dataclasses import dataclass, field

from metamuse._muse_codec import ProtocolMismatch, _bytes_field, _iter_fields

HANDSHAKE_PAYLOAD_MAX_BYTES = 8192


@dataclass(frozen=True)
class StandardBundle:
    key: bytes = field(repr=False)
    nonce: bytes = field(repr=False)


@dataclass(frozen=True)
class ConfidentialChallenge:
    state: int


def _fields(data: bytes) -> dict[int, tuple[int, bytes | int]]:
    if not data or len(data) > HANDSHAKE_PAYLOAD_MAX_BYTES:
        raise ProtocolMismatch("unsupported handshake payload size")
    result = {}
    for number, wire, value in _iter_fields(data):
        if number in result:
            raise ProtocolMismatch("duplicate handshake field")
        result[number] = (wire, value)
    return result


def client_challenge(nonce: bytes) -> bytes:
    if len(nonce) != 32:
        raise ProtocolMismatch("invalid client challenge length")
    return _bytes_field(1, nonce)


def classify_payload(data: bytes) -> StandardBundle | ConfidentialChallenge:
    outer = _fields(data)
    # RV is recognized first. Incomplete or malformed RV cannot become standard.
    if outer.get(2, (None,))[0] == 0:
        state = outer[2][1]
        if (
            outer.get(1, (None,))[0] != 2
            or state not in (1, 2)
            or outer.get(3, (None,))[0] != 2
            or len(outer[3][1]) != 32
            or (state == 1 and 4 in outer)
            or (state == 2 and (outer.get(4, (None,))[0] != 2 or len(outer[4][1]) != 32))
        ):
            raise ProtocolMismatch("unsupported RV challenge")
        if 5 in outer:
            if outer[5][0] != 2:
                raise ProtocolMismatch("unsupported chip binding")
            binding = _fields(outer[5][1])
            if set(binding) != {1, 2} or any(
                wire != 2 or not value for wire, value in binding.values()
            ):
                raise ProtocolMismatch("unsupported chip binding")
        return ConfidentialChallenge(state)
    if set(outer) not in ({2}, {1, 2}) or (1 in outer and outer[1] != (0, 0)):
        raise ProtocolMismatch("unsupported attestation bundle")
    if outer[2][0] != 2:
        raise ProtocolMismatch("unsupported report data input")
    nested = _fields(outer[2][1])
    if set(nested) != {1, 2} or any(
        wire != 2 or len(value) != 32 for wire, value in nested.values()
    ):
        raise ProtocolMismatch("unsupported report data input")
    return StandardBundle(nested[1][1], nested[2][1])
