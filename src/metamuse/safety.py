"""Safety feature seam; implementations belong to this feature owner."""

from __future__ import annotations

from typing import TYPE_CHECKING

from metamuse import models as m

if TYPE_CHECKING:
    from metamuse.app import AppContext
    from metamuse.jobs import Operation


IMPLEMENTED_TOOLS: frozenset[str] = frozenset()


def register_operations(registry: dict[str, Operation] | None = None) -> None:
    """Register this module's operations idempotently; none are implemented yet."""


def import_auth(ctx: AppContext, request: m.AuthImportRequest) -> m.Envelope:
    raise m.CoreError(m.ErrorCode.UNSUPPORTED, "not implemented in this build")


def create_permit(ctx: AppContext, request: m.PermitCreateRequest) -> m.Envelope:
    raise m.CoreError(m.ErrorCode.UNSUPPORTED, "not implemented in this build")


# --- Redaction (B07/B11) ---


def redact_text(text: str) -> tuple[str, list[dict[str, str | int]]]:
    """Best-effort guard, not a guarantee. Records contain only categories/counts.

    Never retain matched values, hashes of secrets, or source offsets in the audit.
    No normalization of the unredacted text is performed.
    """
    import re
    from urllib.parse import unquote_plus

    counts: dict[str, int] = {}

    def replace(pattern: str, category: str, value: str = "[REDACTED]") -> None:
        nonlocal text
        text, count = re.subn(pattern, lambda _: value, text)
        if count:
            counts[category] = counts.get(category, 0) + count

    # Remove whole sensitive header lines, including any cookie attributes.
    replace(r"(?im)^[ \t]*(?:set-cookie|cookie)\s*:[^\r\n]*", "cookie")
    replace(r"(?i)authorization\s*:\s*bearer\s+[^\s,;]+", "authorization")

    token_keys = {
        "access_token",
        "auth_token",
        "token",
        "signature",
        "sig",
        "policy",
        "key-pair-id",
        "awsaccesskeyid",
        "googleaccessid",
    }

    def parameter(match: re.Match) -> str:
        prefix, key, value = match.groups()
        name = unquote_plus(key).lower()
        if name in token_keys or name.startswith(("x-amz-", "x-goog-")):
            if value != "[REDACTED]":
                counts["url_token"] = counts.get("url_token", 0) + 1
            return f"{prefix}{key}=[REDACTED]"
        return match.group(0)

    text = re.sub(r"([?&])([^\s?&=<>\"']+)=([^\s&#<>\"']*)", parameter, text)
    replace(r"\bsk-[A-Za-z0-9_-]{8,}\b", "api_key")
    replace(
        r"(?i)\b(?:session(?:id|_id|_cookie|_token)?|sid|phpsessid|jsessionid|"
        r"asp\.net_sessionid|connect\.sid|next-auth\.session-token|"
        r"auth_cookie|csrftoken|__secure-[\w-]+|__host-[\w-]+)\s*=\s*"
        r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s;,]+)",
        "session_cookie",
    )
    # Cookie strings without a header: two or more semicolon-separated pairs.
    replace(
        r"\b[\w.-]+=[^\s;\r\n]+(?:;[ \t]*[\w.-]+=[^\s;\r\n]+)+",
        "cookie",
    )
    replace(
        r"""["'](?:/(?!/)|[A-Za-z]:[\\/]|\\\\)[^"'\r\n]+["']""",
        "local_path",
    )
    replace(r"(?<![\w:/])/(?!/)[^\s<>\"'`,;\]\[)]+", "local_path")
    replace(r"(?<!\w)[A-Za-z]:[\\/][^\s<>\"'`,;\]\[)]+", "local_path")
    replace(r"\\\\[^\s<>\"'`,;\]\[)]+", "local_path")
    return text, [{"kind": kind, "count": count} for kind, count in sorted(counts.items())]
