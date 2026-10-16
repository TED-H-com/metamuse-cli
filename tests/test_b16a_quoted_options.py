"""Quoted curl options and their values keep independent source delimiters."""

import json
from urllib.parse import parse_qs, quote_plus

import pytest

from metamuse.safety import redact_text


@pytest.mark.parametrize("option", ["--user", "--cookie", "-u", "-b"])
def test_quoted_option_equals_keeps_json_layers_and_public_neighbor(option):
    marker = f"{'OPTION'}_MARKER_EQUALS_VALUE"
    value = "foo=" + marker if option in {"--cookie", "-b"} else "alice:" + marker
    command = 'curl "' + option + '"="' + value + '" https://example.invalid'
    text = json.dumps(json.dumps({"cmd": command, "public": "visible"}))
    output, findings = redact_text(text)
    assert marker not in output and findings
    parsed = json.loads(json.loads(output))
    expected_value = "[REDACTED]" if option in {"--cookie", "-b"} else "alice:[REDACTED]"
    assert parsed == {
        "cmd": 'curl "' + option + '"="' + expected_value + '" https://example.invalid',
        "public": "visible",
    }
    assert redact_text(output) == (output, [])


@pytest.mark.parametrize("option", ["--user", "--cookie", "-u", "-b"])
def test_form_json_quoted_option_keeps_both_argument_boundaries(option):
    marker = f"{'OPTION'}_MARKER_FORM_VALUE"
    value = "foo=" + marker if option in {"--cookie", "-b"} else "alice:" + marker
    command = 'curl "' + option + '" "' + value + '" https://example.invalid'
    document = json.dumps({"cmd": command, "public": "visible"})
    text = "body=" + quote_plus(document, safe="") + "&public=visible"
    output, findings = redact_text(text)
    assert marker not in output and findings
    fields = parse_qs(output, strict_parsing=True)
    assert fields["public"] == ["visible"]
    expected_value = "[REDACTED]" if option in {"--cookie", "-b"} else "alice:[REDACTED]"
    assert json.loads(fields["body"][0]) == {
        "cmd": 'curl "' + option + '" "' + expected_value + '" https://example.invalid',
        "public": "visible",
    }
    assert redact_text(output) == (output, [])


def test_quoted_long_option_keeps_escaped_private_tail_inside_its_argument():
    marker = f"{'OPTION'}_MARKER_ESCAPED_PRIVATE_TAIL"
    command = 'curl "--user" "alice:before' + chr(92) + '"' + marker
    command += '" https://example.invalid'
    output, findings = redact_text(json.dumps({"cmd": command, "public": "visible"}))
    assert marker not in output and findings
    assert json.loads(output) == {
        "cmd": 'curl "--user" "alice:[REDACTED]" https://example.invalid',
        "public": "visible",
    }
    assert redact_text(output) == (output, [])


@pytest.mark.parametrize("option", ["--user", "--cookie"])
def test_long_option_still_protects_values_without_a_curl_token(option):
    marker = f"{'OPTION'}_MARKER_LONG_FLAG_VALUE"
    value = "foo=" + marker if option == "--cookie" else "alice:" + marker
    text = json.dumps({"cmd": 'tool "' + option + '" "' + value + '" public'})
    output, findings = redact_text(text)
    assert marker not in output and findings
    expected_value = "[REDACTED]" if option == "--cookie" else "alice:[REDACTED]"
    assert json.loads(output) == {"cmd": 'tool "' + option + '" "' + expected_value + '" public'}
    assert redact_text(output) == (output, [])


def test_shell_operand_flags_remain_private_after_the_operator():
    marker = f"{'OPTION'}_MARKER_PARAMETER_OPERAND"
    for flag in ("-u", "--user"):
        text = 'curl -b "${C:-' + flag + " alice:" + marker + '}" https://example.invalid'
        output, findings = redact_text(json.dumps({"cmd": text, "public": "visible"}))
        assert marker not in output and findings
        parsed = json.loads(output)
        assert parsed["public"] == "visible"
        assert "https://example.invalid" in parsed["cmd"]
        assert redact_text(output) == (output, [])
