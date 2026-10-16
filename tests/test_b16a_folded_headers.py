"""Complete sensitive header folds, without consuming the next independent line."""

import json

import pytest
from b16a_folded_headers import PUBLIC, URL_CASES, benign, encode, private_url_part, samples
from b16a_r6_context import visible

from metamuse._muse_redact import sanitize_text
from metamuse.safety import redact_text

CASES = [(name, "raw") for name, *_ in samples()] + [
    ("cookie_crlf", representation)
    for representation in ("json", "json_in_json", "pct1", "pct2", "form_plus")
]


@pytest.mark.parametrize(("name", "representation"), CASES)
def test_complete_sensitive_header_fold(name, representation):
    _, text, markers, kind = next(row for row in samples() if row[0] == name)
    text += "\nX-Public: " + PUBLIC
    original = encode(text, representation)
    output, findings = redact_text(original)
    assert not visible(output, markers), (name, representation, output)
    assert findings == [{"kind": kind, "count": 1}]
    assert redact_text(output) == (output, [])
    assert PUBLIC in output
    assert not visible(sanitize_text(original), markers)
    if representation in {"json", "json_in_json"}:
        value = json.loads(output)
        if representation == "json_in_json":
            value = json.loads(value)
        assert PUBLIC in value["log"]


@pytest.mark.parametrize("text", benign())
def test_header_fold_exemptions_and_independent_boundaries(text):
    if "\r\n " in text:
        # A literal exemption followed by a continuation is not the whole value.
        assert redact_text(text) == ("Cookie: [REDACTED]", [{"kind": "cookie", "count": 1}])
    else:
        assert redact_text(text) == (text, [])
    escaped = encode(text, "jsonesc")
    if text != next(iter(benign())):
        # No proven JSON enclosure: deeper escaped line breaks remain data.
        output, findings = redact_text(escaped)
        assert findings
        assert "public" not in output.lower()
        assert redact_text(output) == (output, [])


@pytest.mark.parametrize(("text", "expected"), URL_CASES)
def test_legacy_url_cookie_pair_ownership(text, expected):
    output, findings = redact_text(text)
    assert output == expected
    assert findings == ([{"kind": "cookie", "count": 1}] if output != text else [])
    assert redact_text(output) == (output, [])
    for representation in ("json", "json_in_json", "pct1", "pct2", "pct3", "form_plus"):
        original = encode(text, representation)
        output, findings = redact_text(original)
        if text == expected:
            assert (output, findings) == (original, [])
        else:
            assert not visible(output, (private_url_part(text),))
            assert findings == [{"kind": "cookie", "count": 1}]
            assert not visible(sanitize_text(original), (private_url_part(text),))
        assert redact_text(output) == (output, [])
