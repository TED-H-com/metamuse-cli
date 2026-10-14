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
# One alias grammar for query assignments and raw/truncated JSON. Compact keys
# accept snake_case, camelCase and hyphenated forms without a second key list.
_NAMES = "|".join(
    "[-_]*".join(name) for name in sorted(_EXTRA_KEYS | {"authtoken", "accesstoken", "hatchsess"})
)
_QUERY = re.compile(rf"(?i)\b({_NAMES})\s*=\s*([^&\s\"']+)")
_JSON_SECRET = re.compile(
    rf"""(?i)((?:["'])?\b(?:{_NAMES})(?:["'])?\s*[:=]\s*)"""
    r"""(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[^,}\]&\s]+)"""
)
_WAKE_OBJECT = re.compile(r"\{[^{}]*\}")


def _embedded_wake(match):
    try:
        document = json.loads(match[0])
    except (ValueError, RecursionError):
        return match[0]
    if isinstance(document, dict) and {"vmid", "retrycount"} <= {
        re.sub(r"[^a-z0-9]", "", key.lower()) for key in document
    }:
        return "[REDACTED]"
    return match[0]


_ENVELOPE = re.compile(r"(?i)\b(?:s0:|delegation\.|endorsement\.)[A-Za-z0-9_.~+/=-]+")
_NOTARY = re.compile(r"(?<![A-Za-z0-9_.-])(?:[A-Za-z0-9_-]+\.){5}[A-Za-z0-9_-]+(?![A-Za-z0-9_.-])")


def sensitive_key(key: str) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", key.lower())
    return compact in _EXTRA_KEYS or redaction_sensitive_key(key)


def _replace_known(value: str, secret: str) -> str:
    # Hex and UUID diagnostics have no case semantics; base64 and tokens do.
    if re.fullmatch(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{64}|[0-9a-fA-F-]{36}", secret):
        return re.sub(re.escape(secret), "redacted", value, flags=re.IGNORECASE)
    return value.replace(secret, "redacted")


def sanitize_text(value: str, known: tuple[str, ...] = ()) -> str:
    variants = {
        variant
        for secret in known
        if len(secret) >= 4
        for variant in (secret, quote(secret, safe=""), quote_plus(secret, safe=""))
    }
    for variant in sorted(variants, key=len, reverse=True):
        value = _replace_known(value, variant)
    original = value
    # Decode diagnostic percent-encoding before recognizing envelope/query grammars.
    for _ in range(2):
        value = unquote(value)
    decoded = value
    for secret in sorted(known, key=len, reverse=True):
        if len(secret) >= 4:
            value = _replace_known(value, secret)
    value = _WAKE_OBJECT.sub(_embedded_wake, value)
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
