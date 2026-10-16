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
