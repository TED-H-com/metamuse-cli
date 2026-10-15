"""Long compatibility keys and prefix/escape mutation witnesses."""

import json

import pytest
from test_b16a_round4 import assert_private, marker


@pytest.mark.parametrize("length", [201, 10000])
def test_r38_unbounded_sk_key(length):
    value = "s" + "k-" + "A" * length
    assert_private("key " + value, value)


def test_r311_three_json_escape_layers():
    value = marker()
    text = json.dumps({"password": value})
    for _ in range(3):
        text = json.dumps(text)
    assert_private(text, value)


@pytest.mark.parametrize("prefix", ["b", "p", "a", "a-2"])
@pytest.mark.parametrize("digits", [10, 13])
@pytest.mark.parametrize("groups", [1, 2, 3])
@pytest.mark.parametrize("body", [24, 64])
def test_r311_slack_documented_lengths(prefix, digits, groups, body):
    value = "xox" + prefix + "-" + ("1" * digits + "-") * groups + "A" * body
    assert_private("key " + value, value)


def test_r311_header_consumes_one_complete_value():
    from metamuse import safety

    value = marker()
    text = "Set-Cookie: Set-Cookie: " + value
    assert list(safety._header_spans(text, safety._Syntax(text))) == [
        (len("Set-Cookie: "), len(text), "cookie", "[REDACTED]")
    ]
    assert safety.redact_text(text) == ("Set-Cookie: [REDACTED]", [{"kind": "cookie", "count": 1}])
