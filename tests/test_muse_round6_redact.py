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
