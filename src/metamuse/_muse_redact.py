"""Sanitize gateway JSON/text, including encoded ephemeral connection credentials."""

import json
import re
from typing import Any
from urllib.parse import unquote

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
# The same compact sensitive family is used for JSON, headers, repr and queries.
_ASSIGNMENT = re.compile(
    r"""((?:["'])?([A-Za-z_][A-Za-z0-9_.-]*)(?:["'])?\s*(?:=>|:|=)\s*)"""
    r"""(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[^,}\]&\s]+)"""
)
_XML = re.compile(r"<([A-Za-z_][A-Za-z0-9_.-]*)>(.*?)</\1>", re.DOTALL)
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
    compact = re.sub(r"[^a-z0-9]", "", key.casefold())
    return (
        any(name in compact for name in _EXTRA_KEYS | {"hatchsess"})
        or any(
            family in compact
            for family in (
                "nonce",
                "token",
                "proof",
                "secret",
                "notary",
                "noisekey",
                "attemptid",
                "requestid",
                "recovery",
            )
        )
        or redaction_sensitive_key(key)
    )


def _known_spans(value: str, known: tuple[str, ...]) -> str:
    variants = set()
    for secret in known:
        if len(secret) < 4:
            continue
        variants.add(secret)
        variants.add(secret.replace(" ", "+"))  # quote_plus spelling after percent decoding
        for _ in range(2):
            secret = unquote(secret)
            variants.add(secret)
    spans = []
    for secret in sorted(variants, key=len, reverse=True):
        flags = (
            re.IGNORECASE
            if re.fullmatch(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{64}|[0-9a-fA-F-]{36}", secret)
            else 0
        )
        spans.extend(match.span() for match in re.finditer(re.escape(secret), value, flags))
    # Merge overlaps before editing: replacing one value cannot destroy another match.
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    for start, end in reversed(merged):
        value = value[:start] + "redacted" + value[end:]
    return value


def sanitize_text(value: str, known: tuple[str, ...] = ()) -> str:
    # Decode first, including lowercase escapes, so encoded containment is intact.
    for _ in range(2):
        value = unquote(value)
    value = _known_spans(value, known)
    value = _WAKE_OBJECT.sub(_embedded_wake, value)
    value = _ASSIGNMENT.sub(
        lambda match: match[1] + '"redacted"' if sensitive_key(match[2]) else match[0], value
    )
    value = _XML.sub(
        lambda match: (
            "<" + match[1] + ">redacted</" + match[1] + ">" if sensitive_key(match[1]) else match[0]
        ),
        value,
    )
    value = _ENVELOPE.sub("redacted", value)
    value = _NOTARY.sub("redacted", value)
    return redact_text(value)[0]


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
