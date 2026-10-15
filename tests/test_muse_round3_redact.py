"""Reviewer sentinels are interpolated, synthetic, and never printed."""

import base64
from uuid import UUID

import pytest

from metamuse._muse_redact import _EXTRA_KEYS, sanitize_text
from metamuse.muse import MuseTransport, _Secrets


def test_r3_f_nonce_and_connection_uuid_variants_are_scrubbed():
    nonce = bytes([0xFB, 0xEF, 0xBE] * 10 + [1, 2])
    request = str(UUID(int=0x123456789ABC4DEF8123456789ABCDEF))
    attempt = str(UUID(int=0xABCDEF1234564ABC8ABC123456ABCDEF))
    transport = MuseTransport()
    transport._secrets = _Secrets()
    transport._remember_nonce(nonce)
    transport._remember(request)
    transport._remember(attempt)
    variants = [
        nonce.hex().upper(),
        "".join(char.upper() if i % 2 else char for i, char in enumerate(nonce.hex())),
        base64.b64encode(nonce).decode().rstrip("="),
        request.upper(),
        "".join(char.upper() if i % 2 else char for i, char in enumerate(request)),
        request.replace("-", ""),
        request.replace("-", "").upper(),
        attempt.upper(),
        attempt.replace("-", ""),
        attempt.replace("-", "").upper(),
    ]
    for value in variants:
        assert value not in transport._secrets.text("detail " + value)
        assert value.encode() not in transport._secrets.body(
            ('{"unknown":"' + value + '"}').encode()
        )


@pytest.mark.parametrize("key", sorted(_EXTRA_KEYS))
@pytest.mark.parametrize("shape", ["repr", "header", "spaced_eq", "query"])
def test_r3_f_aliases_in_repr_headers_and_spaced_assignments(key, shape):
    from urllib.parse import quote

    sentinel = "synthetic-" + "review-value-42"
    forms = {
        "repr": "{'" + key + "': '" + sentinel + "'}",
        "header": key + ": " + sentinel,
        "spaced_eq": key + " = " + sentinel,
        "query": "?" + key + "=" + sentinel,
    }
    for value in (forms[shape], quote(forms[shape], safe="")):
        assert sentinel not in sanitize_text(value)


def test_r3_f_known_values_replace_longest_first():
    short = "viewer-" + "fixture"
    long = "legacy" + short + "synthetic-" + "tail-42"
    secrets = _Secrets()
    secrets.add(short)
    secrets.add(long)
    assert "synthetic-tail-42" not in secrets.text("detail " + long)
    assert "synthetic-tail-42" not in sanitize_text("detail " + long, (short, long))
