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
        "hatchsess",
    }
)
# The same compact sensitive family is used for JSON, headers, repr and queries.
_ASSIGNMENT = re.compile(
    r"""(?<![A-Za-z0-9_.-])((?:["'])?([A-Za-z_][A-Za-z0-9_.-]*)(?:["'])?\s*(?:=>|:|=)\s*)"""
    r"""(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[^,}\]&\s]+)"""
)
_XML = re.compile(r"<([A-Za-z_][A-Za-z0-9_.-]*)>([^<]*)</\1>", re.DOTALL)
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
    # Association metadata is public; remembered values remain independently scrubbed.
    if key in {"session_id", "request_marker", "x-request-id", "client_request_id"}:
        return False
    # Split acronym/camelCase boundaries before folding; words such as tokenizer
    # and secretary are not token/secret components.
    separated = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", key)
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", separated)
    components = re.findall(r"[a-z0-9]+", separated.casefold())
    compact = "".join(components)
    # Versioned aliases shipped in the previous grammar retain their meaning.
    if components and re.fullmatch(r"v[0-9]+", components[-1]):
        components.pop()
    endings = {"".join(components[-1:]), "".join(components[-2:])}
    families = {
        "nonce",
        "token",
        "proof",
        "secret",
        "notary",
        "noisekey",
        "attemptid",
        "requestid",
        "recovery",
        "rvkey",
        "recoverykey",
        "wakebody",
    }
    return (
        compact in _EXTRA_KEYS
        or bool(endings & (families | _EXTRA_KEYS))
        or redaction_sensitive_key(key)
    )


def _decoded_levels(value):
    """Keep raw and decoded coordinates: a literal '%' can eat a secret prefix."""
    yield value, None
    positions = None
    for _ in range(2):
        chunks, mapped = [], []
        offset = 0
        for match in re.finditer(r"(?:%[0-9a-fA-F]{2})+", value):
            chunks.append(value[offset : match.start()])
            mapped.extend(
                positions[offset : match.start()]
                if positions is not None
                else ((i, i + 1) for i in range(offset, match.start()))
            )
            decoded = unquote(match[0])
            chunks.append(decoded)
            start = positions[match.start()][0] if positions is not None else match.start()
            end = positions[match.end() - 1][1] if positions is not None else match.end()
            mapped.extend([(start, end)] * len(decoded))
            offset = match.end()
        if offset == 0:
            break
        chunks.append(value[offset:])
        mapped.extend(
            positions[offset:]
            if positions is not None
            else ((i, i + 1) for i in range(offset, len(value)))
        )
        value, positions = "".join(chunks), mapped
        yield value, positions


def _known_spans(value: str, known: tuple[str, ...]):
    variants = set()
    for secret in known:
        if len(secret) < 4:
            continue
        variants.add(secret)
        variants.add(secret.replace(" ", "+"))
        for _ in range(2):
            secret = unquote(secret)
            variants.add(secret)
    for secret in sorted(variants, key=len, reverse=True):
        flags = (
            re.IGNORECASE
            if re.fullmatch(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{64}|[0-9a-fA-F-]{36}", secret)
            else 0
        )
        yield from (match.span() for match in re.finditer(re.escape(secret), value, flags))


def _render_spans(value, spans):
    # One output build. Merge the union before editing so duplicates/containment
    # neither leak a tail nor consume ordinary context beside the secret.
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    chunks, offset = [], 0
    for start, end in merged:
        chunks.extend((value[offset:start], "redacted"))
        offset = end
    chunks.append(value[offset:])
    return "".join(chunks)


def _syntax_spans(value):
    from metamuse._muse_codec import _BEARER, _COOKIE, _PAIR

    for match in re.finditer(
        r"(?i)(?<![a-z0-9_.-])(?:proxy-)?authorization\s*:\s*[^\s]+[ \t]+[^\s,}]+", value
    ):
        yield match.span()
    # Match these before a generic assignment can treat 'Bearer' as the value.
    for pattern in (_BEARER, _COOKIE, _PAIR):
        yield from (match.span() for match in pattern.finditer(value))
    offset = 0
    while match := _ASSIGNMENT.search(value, offset):
        if sensitive_key(match[2]):
            yield match.start() + len(match[1]), match.end()
            offset = match.end()
        else:
            offset = match.start() + len(match[1])
    for match in _XML.finditer(value):
        if sensitive_key(match[1]):
            yield match.start(2), match.end(2)
    for match in _WAKE_OBJECT.finditer(value):
        if _embedded_wake(match) != match[0]:
            yield match.span()
    for pattern in (_ENVELOPE, _NOTARY):
        yield from (match.span() for match in pattern.finditer(value))


def sanitize_text(value: str, known: tuple[str, ...] = ()) -> str:
    spans = []
    for decoded, positions in _decoded_levels(value):
        for start, end in (*_known_spans(decoded, known), *_syntax_spans(decoded)):
            if start == end:
                continue
            spans.append(
                (positions[start][0], positions[end - 1][1])
                if positions is not None
                else (start, end)
            )
    rendered = _render_spans(value, spans)
    # Core historically classifies session_id as a cookie. Protect only these
    # exact public key names; values still pass known-value and core scrubbing.
    protected = {}
    for key in ("session_id", "request_marker", "x-request-id", "client_request_id"):
        placeholder = "musepublicfield" + str(len(protected))
        while placeholder in rendered:
            placeholder += "x"
        protected[placeholder] = key
    by_key = {key: placeholder for placeholder, key in protected.items()}

    def public_key(match):
        key = match[2]
        if key not in {"session_id", "request_marker", "x-request-id", "client_request_id"}:
            return match[0]
        return match[0].replace(key, by_key[key], 1)

    rendered = _ASSIGNMENT.sub(public_key, rendered)
    cleaned = redact_text(rendered)[0]
    for placeholder, key in protected.items():
        cleaned = cleaned.replace(placeholder, key)
    return cleaned


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
