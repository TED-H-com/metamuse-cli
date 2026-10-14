"""Sanitize gateway JSON without destroying its non-secret routing identities."""

from typing import Any

from metamuse.safety import redact_text, redaction_sensitive_key


def sanitize_document(value: Any, known: tuple[str, ...] = (), depth=0) -> Any:
    if depth > 32:
        return None
    if isinstance(value, str):
        for secret in known:
            if len(secret) >= 4:
                value = value.replace(secret, "redacted")
        return redact_text(value)[0]
    if isinstance(value, list):
        return [sanitize_document(child, known, depth + 1) for child in value]
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if key != "session_id" and redaction_sensitive_key(key)
            else sanitize_document(child, known, depth + 1)
            for key, child in value.items()
        }
    return value
