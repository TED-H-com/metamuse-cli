"""Component families, codec differential, and single-pass owned scanning."""

import json
import random

import pytest

from metamuse._muse_codec import redact
from metamuse._muse_redact import sanitize_document, sanitize_text
from metamuse.muse import _Secrets

KEYS = """SECRET_KEY secret_key secretKey aws_secret_access_key AWS_SECRET_ACCESS_KEY
secret_access_key app_secret_key secret_value client_secret_value nonce_b64 nonce_hex
client_nonce_hex serverNonceB64 proof_b64 notary_blob notary_jwt token_value tokenValue
auth_token_b64 noise_key_hex attempt_id_hex request_id_value rv_key_b64 wake_body_json
recovery_code recoveryPhrase tokens secrets proofs requestIds connect_attempt_ids recovery_codes
noise_static_key noisePrivateKey noise_psk access_key passwd ｎｏｎｃｅ to​ken
request_payload_ids noise_private_keys access_static_keys n\u00adonce""".split()
VISIBLE = """session_id request_marker x-request-id X-Request-Id x_request_id client_request_id
vm_id discussion_id job_id max_tokens prompt_tokens completion_tokens total_tokens input_tokens
output_tokens cached_tokens reasoning_tokens max_output_tokens max_completion_tokens
cache_creation_input_tokens cache_read_input_tokens usage.prompt_tokens tokenizer secretary
proofread awake handshake_ms wake_time""".split()


def forms(key, value):
    return [
        sanitize_document({key: value}),
        sanitize_text(f"{key}={value}"),
        sanitize_text(json.dumps({key: value})),
        sanitize_text(f"{key}: {value}"),
        sanitize_text(f"{key} => {value}"),
        sanitize_text(f"<{key}>{value}</{key}>"),
    ]


@pytest.mark.parametrize("key", KEYS)
def test_r6_1_every_component_in_six_forms(key):
    value = "Zq" + "Synthetic42Value"
    for result in forms(key, value):
        assert value not in str(result), (key, result)


@pytest.mark.parametrize("key", VISIBLE)
def test_r6_1_public_fields_in_six_forms(key):
    value = "Zq" + "Visible42Value"
    for result in forms(key, value):
        assert value in str(result), (key, result)
    assert value not in sanitize_text(f"{key}={value}", (value,))


def test_r6_1_unicode_components():
    from metamuse._muse_components import components

    assert components("xQ7cVm2R") == ("x", "q", "7", "c", "vm", "2", "r")
    assert components("CVMRequired HTTPServer ｃｖｍ_required") == (
        "cvm",
        "required",
        "http",
        "server",
        "cvm",
        "required",
    )
    for ignored in ("\u00ad", "\u034f", "\u200b", "\u2064", "\ufe0f", "\U000e0100"):
        assert components("no" + ignored + "nce") == ("nonce",)


def _check(text, secrets):
    codec = redact(text)
    outputs = [sanitize_text(text), _Secrets().text(text), _Secrets().body(text.encode()).decode()]
    for secret in secrets:
        if secret not in codec:
            assert all(secret not in output for output in outputs), (text, secret, outputs)


def test_r6_3_codec_seeded_compositions():
    rng = random.Random(7)
    keys = [
        "auth_token",
        "access_token",
        "hatch_sess",
        "AUTH_TOKEN",
        "Access_Token",
        "x",
        "vm_id",
        "notary_token",
    ]
    separators = ["&", "; ", " ", "\n", ",", "\t", '"', "'", "", "?", "#"]
    for n in range(5000):
        parts, secrets = [], []
        for j in range(rng.randrange(1, 5)):
            secret = "Zq" + "Syn" + str(1000 + n * 10 + j) + "Val"
            secrets.append(secret)
            kind = rng.randrange(7)
            if kind == 0:
                part = f"{rng.choice(keys)}={secret}"
            elif kind == 1:
                part = (
                    rng.choice(["Authorization: ", "authorization:", ""])
                    + rng.choice(["Bearer", "bearer", "BEARER"])
                    + rng.choice([" ", "  ", "\t"])
                    + secret
                )
            elif kind == 2:
                part = (
                    rng.choice(["Cookie", "cookie", "COOKIE"])
                    + ":"
                    + rng.choice(["", " "])
                    + "a=b; hatch_sess="
                    + secret
                )
            elif kind == 3:
                part = "wss://h.example/v1/noise?vm_id=vm1&auth_token=" + secret
            elif kind == 4:
                part = json.dumps({rng.choice(keys): secret})
            elif kind == 5:
                part = "plain words " + secret
            else:
                part = rng.choice(keys) + "%3D" + secret
            parts.append(part)
        _check("".join(part + rng.choice(separators) for part in parts), secrets)


@pytest.mark.parametrize("separator", ['"', "'", "?", "#", ""])
@pytest.mark.parametrize("first", ["auth_token=", "auth_token%3D", "wss://h.example/?auth_token="])
def test_r6_3_quote_stops_value_before_json(first, separator):
    a, b = "Zq" + "First42Value", "Zq" + "Second43Value"
    _check(first + a + separator + json.dumps({"notary_token": b}), [a, b])


@pytest.mark.parametrize("levels", [1, 2])
def test_r6_3_encoded_known_value_needs_decoded_view(levels):
    secret = "Zq" + "Encoded42Value"
    encoded = secret
    for _ in range(levels):
        encoded = "".join(f"%{ord(c):02x}" for c in encoded)
    output = sanitize_text("prefix " + encoded + " suffix", (secret,))
    from urllib.parse import unquote

    assert secret not in unquote(unquote(output))
    assert output.startswith("prefix ") and output.endswith(" suffix")


@pytest.mark.parametrize("levels", [1, 2])
def test_r6_3_encoded_alias_needs_decoded_syntax(levels):
    from urllib.parse import quote, unquote

    secret = "Zq" + "EncodedAlias42Value"
    value = "notary_token=" + secret
    for _ in range(levels):
        value = quote(value, safe="")
    assert secret not in unquote(unquote(sanitize_text(value)))


@pytest.mark.parametrize(
    "key", ["secret.prompt_tokens", "token.max_tokens", "secret.usage.prompt_tokens"]
)
def test_r6_1_sensitive_prefix_is_not_a_usage_count(key):
    value = "Zq" + "CountSecret42Value"
    assert all(value not in str(output) for output in forms(key, value))


@pytest.mark.parametrize("key", ["nonce", "ｎｏｎｃｅ", "to\u200bken", "secret_key"])
def test_r6_1_escaped_json_keys_in_plain_text(key):
    value = "Zq" + "EscapedKey42Value"
    escaped = "".join(f"\\u{ord(c):04x}" for c in key)
    assert value not in sanitize_text('prefix {"' + escaped + '": "' + value + '"} suffix')


@pytest.mark.parametrize("key", ["nonce extra", 'nonce"extra', "nonce\\extra"])
def test_r6_1_quoted_json_key_components(key):
    value = "Zq" + "QuotedKey42Value"
    assert value not in sanitize_text("prefix " + json.dumps({key: value}) + " suffix")


def test_r6_1_long_keys_are_not_retained():
    import gc
    import tracemalloc

    from metamuse._muse_redact import sensitive_key

    sensitive_key("warmup")
    tracemalloc.start()
    try:
        gc.collect()
        before = tracemalloc.get_traced_memory()[0]
        for number in range(32):
            sensitive_key("ordinary" + str(number) + "x" * 65536)
        gc.collect()
        retained = tracemalloc.get_traced_memory()[0] - before
        assert retained < 262144, retained
    finally:
        tracemalloc.stop()
