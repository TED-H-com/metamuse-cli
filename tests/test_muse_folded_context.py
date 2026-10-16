"""Muse must preserve credential context until shared redaction owns its extent."""

import json
from urllib.parse import quote, quote_plus, unquote_plus

import pytest

from metamuse._muse_redact import sanitize_document, sanitize_text
from metamuse.muse import _Secrets
from metamuse.safety import redact_text

FIRST = f"{'Zq71Md'}_FIRST_SYNTHETIC_359821"
TAIL = f"{'Yt82Ne'}_TAIL_SYNTHETIC_468932"
PUBLIC = "PUBLIC_BOUNDARY_7793"
HEADERS = {
    "cookie_crlf": f"Cookie: a={FIRST};\r\n b={TAIL}",
    "cookie_tab": f"cOoKiE: a={FIRST};\n\tb={TAIL}",
    "cookie_colon": f"Cookie: a={FIRST};\r\n b: {TAIL}",
    "cookie_quoted": f'Cookie: "a={FIRST};"\r\n b="left {TAIL} right"',
    "cookie_escaped": f'Cookie: a="left\\"{FIRST}";\n b={TAIL}',
    "set_cookie": f"Set-Cookie: a={FIRST};\r\n b={TAIL}; HttpOnly",
    "cookie_assignment": f"cookie_header=a={FIRST};\n b={TAIL}",
    "authorization": f"Authorization: Bearer {FIRST}\r\n response={TAIL}",
    "authorization_colon": f"Authorization: Basic {FIRST}\n response: {TAIL}",
    "proxy_authorization": f"Proxy-Authorization: Basic {FIRST}\n\t{TAIL}",
}


def encode(text, representation):
    if representation == "json":
        return json.dumps({"log": text})
    if representation == "json_in_json":
        return json.dumps(json.dumps({"log": text}))
    if representation == "jsonesc":
        return json.dumps(text)[1:-1]
    if representation == "pct1":
        return quote(text, safe="")
    if representation == "pct2":
        return quote(quote(text, safe=""), safe="")
    if representation == "form_plus":
        return "q=" + quote_plus(text)
    return text


def assert_private(output, markers=(FIRST, TAIL)):
    for _ in range(4):
        assert all(marker not in output for marker in markers), output
        output = unquote_plus(output)


@pytest.mark.parametrize("header", HEADERS)
def test_complete_fold_reaches_shared_redactor(header):
    text = HEADERS[header] + "\nX-Public: " + PUBLIC
    shared, _ = redact_text(text)
    assert_private(shared)
    secrets = _Secrets()
    for output in (
        sanitize_text(text),
        secrets.text(text),
        secrets.body(text.encode()).decode(),
    ):
        assert_private(output)
        assert PUBLIC in output
        assert sanitize_text(output) == output
    document = json.loads(secrets.body(json.dumps({"log": text}).encode()))
    assert_private(document["log"])
    assert PUBLIC in document["log"]


@pytest.mark.parametrize(
    "representation", ["json", "json_in_json", "jsonesc", "pct1", "pct2", "form_plus"]
)
def test_encoded_fold_retains_header_context(representation):
    for header in ("cookie_crlf", "set_cookie", "authorization", "proxy_authorization"):
        text = encode(HEADERS[header] + "\nX-Public: " + PUBLIC, representation)
        shared = redact_text(text)[0]
        assert_private(shared)
        secrets = _Secrets()
        for output in (
            sanitize_text(text),
            secrets.text(text),
            secrets.body(text.encode()).decode(),
        ):
            assert_private(output)
            # Bare JSON escapes do not prove an enclosing string boundary.
            # Preserve public context whenever the shared grammar proves it.
            if PUBLIC in shared:
                assert PUBLIC in output
            assert sanitize_text(output) == output
        if representation in ("json", "json_in_json"):
            json.loads(secrets.body(text.encode()))


def test_known_values_and_public_fields_survive_reordered_context():
    known = f"{'Lk63Qz'}_KNOWN_SYNTHETIC_882731"
    public_fields = {
        "session_id": "session-visible",
        "request_marker": "marker-visible",
        "x-request-id": "request-visible",
        "client_request_id": "client-visible",
        "vm_id": "vm-visible",
        "discussion_id": "discussion-visible",
        "job_id": "job-visible",
        "usage.prompt_tokens": 17,
    }
    document = {
        **public_fields,
        "detail": HEADERS["cookie_crlf"] + "\nX-Public: " + PUBLIC,
        "reply": "known " + quote(quote(known, safe=""), safe=""),
    }
    result = sanitize_document(document, (known,))
    assert {key: result[key] for key in public_fields} == public_fields
    assert_private(json.dumps(result), (FIRST, TAIL, known))
    assert PUBLIC in result["detail"]
    assert sanitize_document(result, (known,)) == result
    for key in public_fields:
        assert_private(sanitize_text(f"{key}={known}", (known,)), (known,))
    assert known not in str(sanitize_document({known: "public"}, (known,)))
    # The existing structural depth bound still applies.
    assert sanitize_document({"reply": "visible"}, depth=33) is None


def test_private_url_query_and_public_boundaries():
    private = "https://www.facebook.com/watch/?v=111;x=1"
    public = "https://example.invalid/path;public=1"
    for representation in ("raw", "json", "pct1", "pct2", "form_plus"):
        output = sanitize_text(encode(private, representation))
        assert_private(output, ("v=111;x=1",))
        public_output = sanitize_text(encode(public, representation))
        assert public in unquote_plus(unquote_plus(public_output))


@pytest.mark.parametrize(
    "representation", ["raw", "pct1", "pct2", "json", "json_in_json", "form_plus"]
)
def test_encoded_bearer_keeps_original_assignment_context(representation):
    # One inner percent escape and up to two enclosing percent wrappers remain
    # inside the supported budget. A partial shared mask must not erase the
    # Bearer introducer before the complete credential is selected.
    markers = ("ZqSyn2560Val", "ZqSyn2561Val", "ZqSyn2562Val")
    text = encode(
        f"notary_token%3D{markers[0]}authorization:BEARER  {markers[1]}\nbearer\t{markers[2]}\t",
        representation,
    )
    secrets = _Secrets()
    for output in (
        sanitize_text(text),
        secrets.text(text),
        secrets.body(text.encode()).decode(),
    ):
        assert_private(output, markers)
        assert sanitize_text(output) == output
    if representation in ("json", "json_in_json"):
        json.loads(secrets.body(text.encode()))


KNOWN_URL = "wss://ZqKnownHost42.fixture.test/secretPath73?auth_token=ZqKnownTail73"
KNOWN_PARTS = ("ZqKnownHost42", "secretPath73", "ZqKnownTail73")


@pytest.mark.parametrize("representation", ["raw", "pct1", "json"])
def test_known_full_url_survives_partial_shared_query_mask(representation):
    text = (
        json.dumps({"note": KNOWN_URL})
        if representation == "json"
        else encode("note: " + KNOWN_URL, representation)
    )
    secrets = _Secrets()
    secrets.add(KNOWN_URL)
    for output in (
        sanitize_text(text, (KNOWN_URL,)),
        secrets.text(text),
        secrets.body(text.encode()).decode(),
    ):
        assert_private(output, KNOWN_PARTS)
        assert sanitize_text(output, (KNOWN_URL,)) == output
    if representation == "json":
        json.loads(secrets.body(text.encode()))


def test_original_known_values_keys_and_folds_are_suppressed_together():
    private_object = json.dumps({"auth_token": "ZqPrivateTail73", "note": "ZqPrivatePart42"})
    known = (KNOWN_URL, private_object)
    markers = (*KNOWN_PARTS, "ZqPrivateTail73", "ZqPrivatePart42", FIRST, TAIL)
    document = {
        KNOWN_URL: "public-value",
        "note": private_object,
        "detail": HEADERS["cookie_crlf"] + "\nX-Public: " + PUBLIC,
        "session_id": "session-visible",
        "request_marker": "marker-visible",
        "vm_id": "vm-visible",
        "discussion_id": "discussion-visible",
        "job_id": "job-visible",
        "usage.prompt_tokens": 17,
    }
    result = sanitize_document(document, known)
    assert_private(json.dumps(result), markers)
    assert PUBLIC in result["detail"]
    for key in (
        "session_id",
        "request_marker",
        "vm_id",
        "discussion_id",
        "job_id",
        "usage.prompt_tokens",
    ):
        assert result[key] == document[key]
    assert sanitize_document(result, known) == result
    secrets = _Secrets()
    for value in known:
        secrets.add(value)
    result = json.loads(secrets.body(json.dumps(document).encode()))
    assert_private(json.dumps(result), markers)
    assert PUBLIC in result["detail"]


def test_known_values_keep_context_across_overlapping_representations():
    prefix = HEADERS["cookie_crlf"].split("\r\n")[0]
    for representation in ("raw", "json", "json_in_json", "pct2", "form_plus"):
        secrets = _Secrets()
        secrets.add(prefix)
        text = encode(HEADERS["cookie_crlf"] + "\nX-Public: " + PUBLIC, representation)
        for output in (sanitize_text(text, (prefix,)), secrets.body(text.encode()).decode()):
            assert_private(output, ("Cookie", FIRST, TAIL))
            assert PUBLIC in output
            assert sanitize_text(output, (prefix,)) == output
        secrets = _Secrets()
        secrets.add(KNOWN_URL)
        text = encode("note: " + KNOWN_URL, representation)
        for output in (sanitize_text(text, (KNOWN_URL,)), secrets.body(text.encode()).decode()):
            assert_private(output, KNOWN_PARTS)
            assert sanitize_text(output, (KNOWN_URL,)) == output


def test_known_ranges_cross_and_overlap_public_key_placeholders():
    known = "wss://ZqPublicOverlap42.fixture.test/path73?session_id=ZqOpaque42&auth_token=ZqTail73"
    for representation in ("raw", "json", "json_in_json", "pct2", "form_plus"):
        secrets = _Secrets()
        secrets.add(known)
        text = encode("note: " + known, representation)
        for output in (sanitize_text(text, (known,)), secrets.body(text.encode()).decode()):
            assert_private(output, ("ZqPublicOverlap42", "path73", "ZqOpaque42", "ZqTail73"))
            assert sanitize_text(output, (known,)) == output
    # Two private ranges inside the same exempt spelling project to one mask;
    # neither the placeholder nor restoring it may resurrect either fragment.
    for key in ("session_id", "discussion_id"):
        known_parts = (key[1:5], key[4:8])
        for text in (f"{key}=public-value", f"<{key}>public-value</{key}>"):
            output = sanitize_text(text, known_parts)
            assert_private(output, known_parts)
            assert "public-value" in output
            assert sanitize_text(output, known_parts) == output


def test_legacy_known_marker_preserves_shared_masks_and_literal_neighbours():
    known = "a1b2c3d4-0000-0000-0000-000000001234"
    text = (
        "literal [REDACTED] / redacted; id "
        + known
        + " KEEP-THIS-CONTEXT\nAuthorization: Bearer ZqSharedOnly42Synthetic"
    )
    expected = (
        "literal [REDACTED] / redacted; id redacted KEEP-THIS-CONTEXT\nAuthorization: [REDACTED]"
    )
    secrets = _Secrets()
    secrets.add(known)
    for output in (
        sanitize_text(text, (known,)),
        secrets.text(text),
        secrets.body(text.encode()).decode(),
    ):
        assert output == expected
        assert sanitize_text(output, (known,)) == output
    # Exempt fields keep literal marker values; a remembered value still wins.
    assert sanitize_document(
        {"session_id": "redacted", "discussion_id": "[REDACTED]", "job_id": known},
        (known,),
    ) == {"session_id": "redacted", "discussion_id": "[REDACTED]", "job_id": "redacted"}


def test_legacy_known_marker_keeps_encoded_neighbours_and_json_structure():
    known = "OUTsyntheticHEADbcdeTAILsynthetic"
    for representation in ("raw", "json", "json_in_json", "pct1", "pct2", "form_plus"):
        text = encode("left " + known + " right", representation)
        secrets = _Secrets()
        secrets.add(known)
        secrets.add("bcde")
        for output in (
            sanitize_text(text, (known, "bcde")),
            secrets.text(text),
            secrets.body(text.encode()).decode(),
        ):
            decoded = unquote_plus(unquote_plus(output))
            assert "left redacted right" in decoded
            assert "[REDACTED]" not in output
            assert_private(output, (known, "bcde"))
            assert sanitize_text(output, (known, "bcde")) == output
            if representation in ("json", "json_in_json"):
                json.loads(output)
