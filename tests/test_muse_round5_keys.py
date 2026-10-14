"""Sensitive key components preserve ordinary words and correlation metadata."""

import pytest
from metamuse._muse_redact import sanitize_document, sanitize_text
from metamuse.safety import redaction_sensitive_key

VISIBLE = [
    "awake",
    "wake_time",
    "proofread",
    "handshake_ms",
    "max_tokens",
    "tokenizer",
    "usage.prompt_tokens",
    "secretary",
    "request_marker",
    "x-request-id",
    "client_request_id",
    "vm_id",
    "discussion_id",
    "job_id",
    "session_id",
]


@pytest.mark.parametrize("key", VISIBLE)
def test_r5_6_ordinary_components_remain_visible(key):
    value = "ordinary42"
    assert sanitize_document({key: value}) == {key: value}
    assert value in sanitize_text(f"{key}={value}")


@pytest.mark.parametrize(
    "key",
    [
        "server_nonce",
        "x-auth-token",
        "clientNonce",
        "upstreamNotaryToken",
        "server.noise_key",
        "delivery_proof",
        "client_nonce",
        "recovery_key",
        "rv_key",
        "wake_body",
        "connectAttemptId",
        "server_request_id",
        "noise-notary-token_v2",
    ],
)
def test_r5_6_secret_component_suffixes(key):
    value = "synthetic-" + "alias-secret42"
    assert value not in sanitize_text(f"{key} => {value}")
    assert value not in str(sanitize_document({key: value}))


def test_r5_6_policy_remains_core_sensitive():
    assert redaction_sensitive_key("policy")
    assert sanitize_document({"policy": "default"})["policy"] != "default"
