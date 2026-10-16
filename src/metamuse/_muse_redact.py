"""Sanitize gateway JSON/text, including encoded ephemeral connection credentials."""

import json
import re
from functools import lru_cache, wraps
from typing import Any
from urllib.parse import unquote

from metamuse._muse_components import compact, components
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
# Scan a key and separator first. Ordinary values are never scanned here.
_KEY_CHAR = r"[^\s\"'=<>:;,{}\[\]&|?!/]+"
_ASSIGNMENT = re.compile(
    r"(?<![^\s\"'=<>:;,{}\[\]&|?!/])(("
    r"\"[^\"\\]*(?:\\.[^\"\\]*)*\"|'[^'\\]*(?:\\.[^'\\]*)*'|" + _KEY_CHAR + r")\s*(?:=>|:|=)\s*)"
)
_VALUE = re.compile(r"\"[^\"\\]*(?:\\.[^\"\\]*)*\"|'[^'\\]*(?:\\.[^'\\]*)*'|[^,}\]&\s\"']+")
_XML_KEY = re.compile(r"<([^\s<>/]+)>")
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
# The shared scanner owns complete header folds and their enclosing boundaries.
# Erasing these introducers first would leave their continuation values untyped.
_HEADER_CONTEXT_KEYS = frozenset(
    ("cookie", "setcookie", "cookieheader", "authorization", "proxyauthorization")
)


_COUNTS = frozenset(
    (
        "max_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "input_tokens",
        "output_tokens",
        "cached_tokens",
        "reasoning_tokens",
        "max_output_tokens",
        "max_completion_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    )
)
_EXEMPT = frozenset(
    compact(key)
    for key in (
        *_COUNTS,
        "session_id",
        "request_marker",
        "x-request-id",
        "client_request_id",
        "vm_id",
        "discussion_id",
        "job_id",
    )
)
_SINGLES = frozenset(("nonce", "token", "proof", "secret", "notary", "recovery", "psk", "passwd"))
_PAIRS = (
    ("noise", "key"),
    ("attempt", "id"),
    ("request", "id"),
    ("rv", "key"),
    ("wake", "body"),
    ("access", "key"),
)


_COUNT_COMPACTS = frozenset(compact(count) for count in _COUNTS)


def _short_key_cache(fn):
    # Bound retained bytes as well as entry count. Large authenticated keys are
    # scanned once and discarded, rather than accumulating across connections.
    cached = lru_cache(maxsize=2048)(fn)

    @wraps(fn)
    def classify(key):
        return cached(key) if len(key) <= 256 else fn(key)

    return classify


@_short_key_cache
def public_key(key: str) -> bool:
    folded = compact(key)
    return folded in _EXEMPT or folded in {"usage" + count for count in _COUNT_COMPACTS}


@_short_key_cache
def sensitive_key(key: str) -> bool:
    parts = components(key)
    if public_key(key):
        return False
    legacy = parts[:-2] if len(parts) >= 2 and parts[-2] == "v" and parts[-1].isdigit() else parts
    singular = tuple(part[:-1] if part.endswith("s") else part for part in parts)
    return (
        "".join(parts) in _EXTRA_KEYS
        or any("".join(legacy[-n:]) in _EXTRA_KEYS for n in (1, 2, 3))
        or any(
            part in _SINGLES or raw in _SINGLES for raw, part in zip(parts, singular, strict=True)
        )
        or any(
            first in parts
            and any(part in (second, second + "s") for part in parts[parts.index(first) + 1 :])
            for first, second in _PAIRS
        )
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


def _merged_spans(spans):
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _render_spans(value, spans):
    # One output build. Merge the union before editing so duplicates/containment
    # neither leak a tail nor consume ordinary context beside the secret.
    chunks, offset = [], 0
    for start, end in _merged_spans(spans):
        chunks.extend((value[offset:start], "redacted"))
        offset = end
    chunks.append(value[offset:])
    return "".join(chunks)


def _text_key(match):
    token = match[2]
    if token.startswith(('"', "'")):
        try:
            return json.loads(token if token[0] == '"' else '"' + token[1:-1] + '"')
        except ValueError:
            return token[1:-1]
    return token


def _syntax_spans(value):
    from metamuse._muse_codec import _BEARER, _COOKIE, _PAIR

    # Preserve shared header masks and their enclosing boundaries. The codec
    # fallback still covers untyped text without consuming a masked Cookie line.
    for match in _BEARER.finditer(value):
        yield match.end(1), match.end()
    for match in _COOKIE.finditer(value):
        if not match[0].split(":", 1)[1].lstrip().startswith("[REDACTED]"):
            yield match.span()
    yield from (match.span() for match in _PAIR.finditer(value))
    offset = 0
    while match := _ASSIGNMENT.search(value, offset):
        offset = match.end()
        key = _text_key(match)
        if sensitive_key(key) and compact(key) not in _HEADER_CONTEXT_KEYS:
            body = _VALUE.match(value, offset)
            if body is not None:
                yield match.start(), body.end()
                offset = body.end()
    offset = 0
    while match := _XML_KEY.search(value, offset):
        offset = match.end()
        if sensitive_key(match[1]):
            end = value.find("<", offset)
            if end < 0:
                end = len(value)
            yield offset, end
            offset = end
    for match in _WAKE_OBJECT.finditer(value):
        if _embedded_wake(match) != match[0]:
            yield match.span()
    for pattern in (_ENVELOPE, _NOTARY):
        yield from (match.span() for match in pattern.finditer(value))


def _public_view(value):
    # Core historically classifies session_id as a cookie. Protect only these
    # public key spellings. Retain sparse edits to project known private ranges.
    keys = [
        (match.start(2), match.end(2))
        for match in _ASSIGNMENT.finditer(value)
        if public_key(_text_key(match))
    ]
    keys.extend(
        (match.start(2), match.end(2))
        for match in re.finditer(r"<(/?)([^\s<>/]+)>", value)
        if public_key(match[2])
    )
    chunks, edits, protected = [], [], {}
    offset = shift = 0
    for start, end in _merged_spans(keys):
        placeholder = "musepublicfield" + str(len(protected)) + "end"
        protected[placeholder] = value[start:end]
        chunks.extend((value[offset:start], placeholder))
        edits.append((start, end, start + shift, start + shift + len(placeholder)))
        shift += len(placeholder) - (end - start)
        offset = end
    chunks.append(value[offset:])
    return "".join(chunks), edits, protected


def _project_spans(spans, edits):
    """Project ordered private ranges through public spelling edits in one walk."""
    index = shift = 0
    for start, end in spans:
        while index < len(edits) and edits[index][1] <= start:
            _, old_end, _, new_end = edits[index]
            shift = new_end - old_end
            index += 1
        first = start + shift
        if index < len(edits) and edits[index][0] <= start < edits[index][1]:
            first = edits[index][2]
        finish = end + shift
        while index < len(edits) and edits[index][0] < end:
            _, old_end, _, new_end = edits[index]
            finish = new_end if end < old_end else end + new_end - old_end
            if end < old_end:
                break
            shift = new_end - old_end
            index += 1
        yield first, finish


def _original_known_spans(value, known):
    spans = []
    if known:
        for decoded, positions in _decoded_levels(value):
            for start, end in _known_spans(decoded, known):
                spans.append(
                    (positions[start][0], positions[end - 1][1])
                    if positions is not None
                    else (start, end)
                )
    return _merged_spans(spans)


def _shared_redaction(value, known):
    from metamuse._muse_codec import _BEARER

    # Select complete known values before even public-key placeholders can hide
    # their spelling. Shared raw/canonical views retain credential introducers
    # until all selected ranges have been united in the original coordinates.
    original_spans = _original_known_spans(value, known)
    rendered, edits, protected = _public_view(value)
    projected = _merged_spans(_project_spans(original_spans, edits))

    def private_spans(view, *, original):
        spans = [*(projected if original else ()), *_known_spans(view, known)]
        spans.extend(
            (match.end(1), match.end())
            for match in _BEARER.finditer(view)
            if view[match.end(1) : match.end()] != "redacted"
        )
        return _merged_spans(spans)

    cleaned = redact_text(rendered, private_spans=private_spans, private_placeholder="redacted")[0]
    # One replacement pass even when there are many public assignment keys.
    if protected:
        cleaned = re.sub(
            r"musepublicfield[0-9]+end", lambda match: protected.get(match[0], match[0]), cleaned
        )
    return cleaned


def sanitize_text(value: str, known: tuple[str, ...] = ()) -> str:
    # The shared scanner must see the credential context, including
    # folds, before an owned match can erase a header or enclosing assignment.
    value = _shared_redaction(value, known)
    spans = []
    for decoded, positions in _decoded_levels(value):
        whole_wake = _WAKE_OBJECT.fullmatch(decoded)
        if whole_wake is not None and _embedded_wake(whole_wake) != decoded:
            return "[REDACTED]"
        for start, end in (*_known_spans(decoded, known), *_syntax_spans(decoded)):
            if start == end:
                continue
            spans.append(
                (positions[start][0], positions[end - 1][1])
                if positions is not None
                else (start, end)
            )
    return _render_spans(value, spans)


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
