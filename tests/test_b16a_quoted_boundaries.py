"""Curl argument masks retain whole source quote escapes and public neighbors."""

import json
from urllib.parse import quote, unquote

import pytest

from metamuse.safety import redact_text


@pytest.mark.parametrize("layers", [2, 3])
def test_quoted_user_keeps_each_json_wrapper_and_public_sibling(layers):
    marker = f"{'QUOTED'}_MARKER_WRAPPED_ARGUMENT"
    command = 'curl -u "John Doe:' + marker + '" https://example.invalid'
    expected = 'curl -u "John Doe:[REDACTED]" https://example.invalid'
    text = json.dumps({"cmd": command, "public": "visible"})
    for _ in range(layers - 1):
        text = json.dumps(text)
    output, findings = redact_text(text)
    assert marker not in output
    assert findings == [{"kind": "sensitive_field", "count": 1}]
    parsed = output
    for _ in range(layers):
        parsed = json.loads(parsed)
    assert parsed == {"cmd": expected, "public": "visible"}
    assert redact_text(output) == (output, [])


@pytest.mark.parametrize("layers", [1, 2])
def test_percent_json_user_keeps_source_delimiters_and_public_sibling(layers):
    marker = f"{'QUOTED'}_MARKER_PERCENT_ARGUMENT"
    text = json.dumps(
        {"cmd": 'curl -u "John Doe:' + marker + '" https://example.invalid', "public": "visible"}
    )
    for _ in range(layers):
        text = quote(text, safe="")
    output, findings = redact_text(text)
    assert marker not in output
    assert findings == [{"kind": "sensitive_field", "count": 1}]
    parsed = output
    for _ in range(layers):
        parsed = unquote(parsed)
    assert json.loads(parsed) == {
        "cmd": 'curl -u "John Doe:[REDACTED]" https://example.invalid',
        "public": "visible",
    }
    assert redact_text(output) == (output, [])


def test_shell_escaped_quote_inside_json_argument_keeps_the_private_tail():
    marker = f"{'QUOTED'}_MARKER_ESCAPED_QUOTE_TAIL"
    command = 'curl -u "alice:before' + chr(92) + '"' + marker + '" https://example.invalid'
    output, findings = redact_text(json.dumps({"cmd": command, "public": "visible"}))
    assert marker not in output
    assert findings == [{"kind": "sensitive_field", "count": 1}]
    assert json.loads(output) == {
        "cmd": 'curl -u "alice:[REDACTED]" https://example.invalid',
        "public": "visible",
    }
    assert redact_text(output) == (output, [])


def test_invalid_quote_follower_does_not_expose_the_argument_tail():
    marker = f"{'QUOTED'}_MARKER_INVALID_CLOSER_TAIL"
    command = 'curl -u "alice:before"-' + marker + '" https://example.invalid'
    output, findings = redact_text(json.dumps({"cmd": command, "public": "visible"}))
    assert marker not in output
    assert findings == [{"kind": "sensitive_field", "count": 1}]
    assert json.loads(output) == {
        "cmd": 'curl -u "alice:[REDACTED]" https://example.invalid',
        "public": "visible",
    }
    assert redact_text(output) == (output, [])


def test_unmatched_argument_quote_retains_the_conservative_extent():
    marker = f"{'QUOTED'}_MARKER_UNMATCHED_ARGUMENT"
    output, findings = redact_text('curl -u "alice:' + marker + " https://example.invalid")
    assert output == 'curl -u "alice:[REDACTED]'
    assert findings == [{"kind": "sensitive_field", "count": 1}]
    assert redact_text(output) == (output, [])


def test_json_cookie_argument_preserves_its_complete_closing_escape():
    marker = f"{'QUOTED'}_MARKER_COOKIE_ARGUMENT"
    text = json.dumps(
        {"cmd": 'curl -b " foo=' + marker + ' " https://example.invalid', "public": "visible"}
    )
    output, findings = redact_text(text)
    assert marker not in output
    assert findings == [{"kind": "cookie", "count": 1}]
    assert json.loads(output) == {
        "cmd": 'curl -b "[REDACTED]" https://example.invalid',
        "public": "visible",
    }
    assert redact_text(output) == (output, [])
