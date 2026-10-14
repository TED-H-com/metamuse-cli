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


def redaction_sensitive_key(key: str) -> bool:
    """ASCII credential names only; normalize percent encoding and -/_ spelling."""
    import re
    from urllib.parse import unquote_plus

    decoded = unquote_plus(key)
    if re.fullmatch(r"[A-Za-z0-9_.-]+", decoded, flags=re.ASCII) is None:
        return False
    name = decoded.lower().replace("-", "").replace("_", "")
    return name in {
        "headers",
        "authorization",
        "cookie",
        "setcookie",
        "accesstoken",
        "authtoken",
        "apikey",
        "token",
        "refreshtoken",
        "idtoken",
        "clientsecret",
        "secret",
        "password",
        "privatekey",
        "session",
        "sessionid",
        "sessioncookie",
        "sessiontoken",
        "sid",
        "phpsessid",
        "jsessionid",
        "asp.netsessionid",
        "connect.sid",
        "csrftoken",
        "nextauth.sessiontoken",
        "xs",
        "cuser",
        "datr",
        "localpath",
        "signature",
        "sig",
        "policy",
        "keypairid",
        "awsaccesskeyid",
        "googleaccessid",
    } or decoded.lower().startswith(("x-amz-", "x-goog-", "__secure-", "__host-"))


def redact_text(text: str) -> tuple[str, list[dict[str, str | int]]]:
    """Syntax-aware best-effort guard, not a guarantee. Audits contain no values."""
    import re
    from urllib.parse import unquote_plus

    counts: dict[str, int] = {}

    def count(kind: str) -> None:
        counts[kind] = counts.get(kind, 0) + 1

    def replace(pattern: str, kind: str, replacement=None) -> None:
        nonlocal text

        def substitute(match):
            result = replacement(match) if replacement else "[REDACTED]"
            if result != match.group(0):
                count(kind)
            return result

        text = re.sub(pattern, substitute, text, flags=re.ASCII)

    # Replace JSON/string-literal values, preserving delimiters and surrounding syntax.
    def json_value(match):
        if redaction_sensitive_key(match["key"]):
            return (
                match["q"]
                + match["key"]
                + match["q"]
                + match["gap"]
                + match["vq"]
                + "[REDACTED]"
                + match["vq"]
            )
        return match.group(0)

    replace(
        r"""(?P<q>["'])(?P<key>[A-Za-z0-9_.%\-]+)(?P=q)(?P<gap>\s*:\s*)"""
        r"""(?P<vq>["'])(?P<value>(?:\\[^\r\n]|(?!(?P=vq))[^\\\r\n])*?)(?P=vq)""",
        "sensitive_field",
        json_value,
    )
    # Headers, including label prefixes, quoted cookie values and folded continuations.
    quoted = r"""(?:"[^"\r\n]*"|'[^'\r\n]*')"""
    header_body = r"(?:(?<![A-Za-z0-9_.-])[A-Za-z0-9_.-]+=" + quoted + r"""|[^\r\n"'}\]])*"""
    replace(
        r"(?im)(?<![A-Za-z0-9_.-])(?:set-cookie|cookie)[ \t]*:[ \t]*"
        + header_body
        + r"(?:\r?\n[ \t]+"
        + header_body
        + r")*",
        "cookie",
    )
    replace(
        r"(?i)(?<![A-Za-z0-9_.-])authorization[ \t]*:[ \t]*"
        r"(?:bearer|basic|token|digest|aws4-hmac-sha256)[ \t]+"
        + header_body
        + r"(?:\r?\n[ \t]+"
        + header_body
        + r")*",
        "authorization",
    )

    # Locate separators without swallowing outer redirect values, so encoded nested
    # query strings are examined too. Only secret values are replaced.
    parameters = re.compile(
        r"(?:[?&#]|%3f|%26|%23)"
        r"(?P<key>(?:(?!%3d|%26|%3f|%23)(?:[A-Za-z0-9_.-]|%[0-9a-f]{2}))+)"
        r"(?:=|%3d)",
        re.IGNORECASE | re.ASCII,
    )
    ending = re.compile(r"""[\s&#"'<>{}\]]|%26|%23|%3f""", re.IGNORECASE | re.ASCII)
    signed = {
        "oh",
        "oe",
        "sp",
        "sv",
        "sr",
        "se",
        "st",
        "skoid",
        "sktid",
        "skt",
        "ske",
        "sks",
        "skv",
    }
    spans = []
    for match in parameters.finditer(text):
        if text.startswith("[REDACTED]", match.end()):
            continue
        name = unquote_plus(match["key"]).lower()
        if redaction_sensitive_key(name) or name in signed:
            end_match = ending.search(text, match.end())
            end = end_match.start() if end_match else len(text)
            spans.append((match.end(), end))
    for start, end in reversed(spans):
        if text[start:end] != "[REDACTED]":
            text = text[:start] + "[REDACTED]" + text[end:]
            count("url_token")

    value = quoted + r"""|[^\s;,"'{}\]]+"""

    def session(match):
        if redaction_sensitive_key(match["key"]):
            return "[REDACTED]"
        return match.group(0)

    replace(
        r"(?i)(?<![A-Za-z0-9_.%?&#-])(?P<key>[A-Za-z_][A-Za-z0-9_.%\-]*)[ \t]*=[ \t]*(?:"
        + value
        + ")",
        "session_cookie",
        session,
    )
    replace(
        r"(?<![A-Za-z0-9_.-])[A-Za-z0-9_.-]+=(?:" + value + ")"
        r"(?:;[ \t]*[A-Za-z0-9_.-]+=(?:" + value + "))+",
        "cookie",
    )
    replace(r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])", "api_key")

    roots = (
        r"/(?:tmp|var|etc|home|Users|opt|usr|proc|sys|dev|run|srv|mnt|media|root|private|"
        r"Library|Volumes|app|workspace)(?:/|\b)"
    )
    plausible = r"(?:" + roots + r"|/[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+|[A-Za-z]:[\\/]|\\\\)"
    replace(
        r"""(?P<q>["'])(?P<path>(?:/[^"'\r\n]+/[^"'\r\n]+|"""
        + plausible
        + r"""[^"'\r\n]*))(?P=q)""",
        "local_path",
        lambda match: match["q"] + "[REDACTED]" + match["q"],
    )
    replace(
        r"(?<![A-Za-z0-9:/])" + plausible + r"""[^\s<>"'`,;{}\]\[)]+""",
        "local_path",
    )
    return text, [{"kind": kind, "count": n} for kind, n in sorted(counts.items())]
