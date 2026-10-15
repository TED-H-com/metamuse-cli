"""Known encoded spans and the documented alias grammar do not leak tails."""

import json
import re
from urllib.parse import quote, quote_plus, unquote

import pytest

from metamuse._muse_redact import sanitize_document, sanitize_text

KEYS = [
    "notary_token",
    "notaryToken",
    "noise-notary-token",
    "NOTARY_TOKEN",
    "connect_attempt_id",
    "recovery_key",
    "client_nonce",
    "rv_key",
    "endorsement",
    "proof",
    "handshake",
    "wake_body",
    "access_token",
    "auth_token",
    "hatch_sess",
]


def shapes(key, value):
    camel_prefixed = "upstream" + key[:1].upper() + key[1:]
    return {
        "repr": "{'" + key + "': '" + value + "'}",
        "header": key + ": " + value,
        "spaced_eq": key + " = " + value,
        "query": "wss://example.invalid/?" + key + "=" + value,
        "json": json.dumps({key: value}),
        "tab_colon": key + "\t:\t" + value,
        "arrow": key + " => " + value,
        "xml": "<" + key + ">" + value + "</" + key + ">",
        "prefix_snake_eq": "x_" + key + "=" + value,
        "prefix_camel_colon": camel_prefixed + ": " + value,
        "suffix_eq": key + "_v2=" + value,
        "dotted_eq": key.replace("_", ".") + "=" + value,
        "json_prefixed": json.dumps({"server_" + key: value}),
        "pct_eq": key + "%3D" + value,
        "quoted_space_value": key + '="' + value + ' tail"',
    }


@pytest.mark.parametrize(
    "key",
    KEYS
    + [
        "server_nonce",
        "handshake_nonce",
        "rv_proof",
        "upstream_notary_token",
        "notary",
        "noise_key",
        "attempt_id",
        "request_id",
        "recovery",
        "x-auth-token",
    ],
)
def test_r4_9_every_alias_text_and_json_form(key):
    sentinel = "synthetic-" + "alias-value-42"
    for shape, text in shapes(key, sentinel).items():
        if key == "request_id" and shape == "prefix_snake_eq":
            assert sentinel in sanitize_text(text)  # compact x-request-id association exemption
            continue
        assert sentinel not in sanitize_text(text), shape
    assert sentinel not in str(sanitize_document({key: sentinel}))


@pytest.mark.parametrize("encoding", ["raw", "quote", "plus", "double", "lower"])
@pytest.mark.parametrize("reverse", [False, True])
def test_r4_9_decode_before_containment_replacement(encoding, reverse):
    short = "synthetic" + "/" + "short42"
    tail = "tail" + "Marker77"
    long = short + "/ " + tail
    known = (long, short) if reverse else (short, long)
    encoded = {
        "raw": long,
        "quote": quote(long, safe=""),
        "plus": quote_plus(long, safe=""),
        "double": quote(quote(long, safe=""), safe=""),
        "lower": re.sub(r"%[A-F0-9]{2}", lambda m: m[0].lower(), quote(long, safe="")),
    }[encoding]
    output = sanitize_text("diagnostic " + encoded, known)
    assert tail not in unquote(unquote(output))
    assert short not in output


def test_r4_9_union_of_overlapping_known_spans():
    first = "AAAAAAAA" + "-ov-" + "1111"
    second = "1111" + "-ov-" + "BBBBBBBB"
    for known in ((first, second), (second, first)):
        output = sanitize_text("x " + first + second[4:] + " y", known)
        assert "AAAAAAAA" not in output and "BBBBBBBB" not in output


def test_r4_9_ordinary_identifiers_remain_visible():
    document = {"vm_id": "vm1", "discussion_id": "discussion42", "job_id": "job42"}
    assert sanitize_document(document) == document
    for key, value in document.items():
        assert value in sanitize_text(f"{key}={value}")
