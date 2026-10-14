"""Sanitize gateway JSON/text, including encoded ephemeral connection credentials."""

import json
import re
from typing import Any
from urllib.parse import quote, quote_plus, unquote

from metamuse.safety import redact_text, redaction_sensitive_key

_EXTRA_KEYS = frozenset(
    {
        "notarytoken",
        "noisenotarytoken",
        "connectattemptid",
        "wake",
        "wakebody",
        "clientnonce",
        "nonce",
        "proof",
        "handshake",
        "recoverykey",
        "rvkey",
        "endorsement",
    }
)
_QUERY = re.compile(
    r"(?i)\b(auth_token|access_token|notary_token|connect_attempt_id|hatch_sess)=([^&\s\"']+)"
)
_JSON_SECRET = re.compile(
    r'(?i)("(?:notary_token|notaryToken|noiseNotaryToken|connect_attempt_id|client_nonce|nonce|proof|recovery_key)"\s*:\s*)"(?:[^"\\]|\\.)*"'
)

_ENVELOPE = re.compile(r"(?i)\b(?:s0:|delegation\.|endorsement\.)[A-Za-z0-9_.~+/=-]+")
_NOTARY = re.compile(r"(?<![A-Za-z0-9_.-])(?:[A-Za-z0-9_-]+\.){5}[A-Za-z0-9_-]+(?![A-Za-z0-9_.-])")


def sensitive_key(key: str) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", key.lower())
    return compact in _EXTRA_KEYS or redaction_sensitive_key(key)


def sanitize_text(value: str, known: tuple[str, ...] = ()) -> str:
    for secret in known:
        if len(secret) >= 4:
            for variant in (secret, quote(secret, safe=""), quote_plus(secret, safe="")):
                value = value.replace(variant, "redacted")
    original = value
    # Decode diagnostic percent-encoding before recognizing envelope/query grammars.
    for _ in range(2):
        value = unquote(value)
    decoded = value
    for secret in known:
        if len(secret) >= 4:
            value = value.replace(secret, "redacted")
    if '"retry_count"' in value and '"vm_id"' in value:
        try:
            document = json.loads(value)
        except (ValueError, RecursionError):
            document = None
        if isinstance(document, dict) and "vm_id" in document and "retry_count" in document:
            return "[REDACTED]"
    value = _JSON_SECRET.sub(lambda match: match[1] + '"redacted"', value)
    value = _QUERY.sub(lambda match: match[1] + "=redacted", value)
    value = _ENVELOPE.sub("redacted", value)
    value = _NOTARY.sub("redacted", value)
    cleaned = redact_text(value)[0]
    return redact_text(original)[0] if cleaned == decoded else cleaned


def sanitize_document(value: Any, known: tuple[str, ...] = (), depth=0) -> Any:
    if depth > 32:
        return None
    if isinstance(value, str):
        return sanitize_text(value, known)
    if isinstance(value, list):
        return [sanitize_document(child, known, depth + 1) for child in value]
    if isinstance(value, dict):
        if "vm_id" in value and "retry_count" in value:
            return "[REDACTED]"
        return {
            sanitize_text(key, known): "[REDACTED]"
            if key != "session_id" and sensitive_key(key)
            else sanitize_document(child, known, depth + 1)
            for key, child in value.items()
        }
    return value
