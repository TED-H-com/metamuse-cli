"""Header value quotes own inner punctuation without losing public boundaries."""

import json
from urllib.parse import quote, quote_plus, unquote, unquote_plus

import pytest

from metamuse.safety import redact_text


def encode(text, representation):
    if representation == "json":
        return json.dumps({"log": text, "public": "sibling"})
    if representation == "json2":
        return json.dumps(json.dumps({"log": text, "public": "sibling"}))
    if representation == "pct2":
        return quote(quote(text, safe=""), safe="")
    if representation == "form":
        return "q=" + quote_plus(text)
    assert representation == "raw"
    return text


def decode(text, representation):
    if representation in {"json", "json2"}:
        parsed = json.loads(text)
        if representation == "json2":
            parsed = json.loads(parsed)
        assert parsed["public"] == "sibling"
        return parsed["log"]
    if representation == "pct2":
        return unquote(unquote(text))
    if representation == "form":
        assert text.startswith("q=")
        return unquote_plus(text[2:])
    return text


def assert_private(text, representation, markers):
    output, findings = redact_text(encode(text, representation))
    for marker in markers:
        assert marker not in output
        assert not any(marker[i : i + 6] in output for i in range(len(marker) - 5))
    assert findings == [{"kind": "cookie", "count": 1}]
    assert redact_text(output) == (output, [])
    return decode(output, representation)


@pytest.mark.parametrize("representation", ["raw", "json2", "pct2", "form"])
@pytest.mark.parametrize(("opening", "closing"), [("(", ")"), ("[", "]"), ("{", "}")])
def test_inner_header_quote_keeps_owned_outer_closer_private(opening, closing, representation):
    marker = f"{'Innermost'}_Synthetic_7319"
    text = opening + "Cookie: 'pa" + closing + " " + marker + "'" + closing + " public"
    assert assert_private(text, representation, [marker]) == (
        opening + "Cookie: [REDACTED]" + closing + " public"
    )


@pytest.mark.parametrize(
    ("opening", "payload", "closing"),
    [
        ("'", "pa) escaped\\' ", "'"),
        ('"', 'pa] doubled"" ', '"'),
        ("`", "pa} ", "`"),
        ("“", "pa) ”-", "”"),
    ],
)
def test_header_own_quote_escape_forms_preserve_public_neighbor(opening, payload, closing):
    marker = f"{'Innermost'}_Synthetic_7319"
    text = "(Cookie: " + opening + payload + marker + closing + ") public"
    assert assert_private(text, "raw", [marker]) == "(Cookie: [REDACTED]) public"


@pytest.mark.parametrize("representation", ["raw", "json"])
def test_unmatched_header_quote_keeps_tail_private_with_proven_enclosure(representation):
    marker = f"{'Innermost'}_Synthetic_7319"
    text = "(Cookie: 'pa) " + marker + "\nX-Public: visible"
    assert assert_private(text, representation, [marker]) == (
        "(Cookie: [REDACTED]\nX-Public: visible"
    )


@pytest.mark.parametrize("representation", ["raw", "json", "json2"])
def test_header_own_quote_keeps_fold_and_independent_logical_line(representation):
    first = f"{'Innermost'}_First_7319"
    tail = f"{'Continuation'}_Tail_8462"
    text = "Cookie: 'pa) " + first + "'\r\n b=" + tail + "\nX-Public: visible"
    assert assert_private(text, representation, [first, tail]) == (
        "Cookie: [REDACTED]\nX-Public: visible"
    )


@pytest.mark.parametrize(
    ("header", "kind"), [("Set-Cookie", "cookie"), ("Authorization", "authorization")]
)
def test_related_header_own_quote_preserves_public_neighbor(header, kind):
    marker = f"{'Innermost'}_Synthetic_7319"
    text = "[" + header + ': "pa] ' + marker + '"] public'
    output, findings = redact_text(text)
    assert output == "[" + header + ": [REDACTED]] public"
    assert marker not in output
    assert findings == [{"kind": kind, "count": 1}]
    assert redact_text(output) == (output, [])


def test_unquoted_header_still_uses_outer_owner_and_quoted_punctuation_stays_private():
    marker = f"{'Innermost'}_Synthetic_7319"
    text = "(Cookie: " + marker + ") public"
    assert assert_private(text, "raw", [marker]) == "(Cookie: [REDACTED]) public"
    quoted = "(Cookie: 'pa) ] } , ; & : " + marker + "') public"
    assert assert_private(quoted, "raw", [marker]) == "(Cookie: [REDACTED]) public"
