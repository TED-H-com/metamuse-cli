"""Quoted arguments keep their own layer even inside another quoted document."""

import json
from urllib.parse import quote

import pytest

from metamuse.safety import redact_text

RULES = (
    "curl " + '-u "John Doe:{v}" https://example.invalid',
    'curl "-u" "alice:{v}" https://example.invalid',
    'curl -b " foo={v} " https://example.invalid',
    'curl "--user" "alice:{v}" https://example.invalid',
    'curl "--cookie" "foo={v}" https://example.invalid',
    'sshpass "-p" "{v}" ssh host',
    'sshpass -p"{v}" ssh host',
    'mysql "-p{v}" db',
    'curl --cookie " foo={v} " https://example.invalid',
    'sshpass -p " {v} " ssh host',
    'mysql -p" {v} " db',
    'CREATE USER u IDENTIFIED BY " {v} "',
    'ALTER USER u WITH PASSWORD " {v} "',
)


def context(text, name):
    if name == "shell":
        return "'" + text + "'"
    if name.startswith("percent"):
        for _ in range(int(name[-1])):
            text = quote(text, safe="")
    else:
        for _ in range(int(name)):
            text = json.dumps({"cmd": text})
    return text


@pytest.mark.parametrize("rule", RULES)
@pytest.mark.parametrize("layer", ["0", "1", "2", "3", "shell", "percent1", "percent2"])
def test_r42_quoted_arguments_at_every_layer(rule, layer):
    marker = f"{'QUOTE'}_MARKER_ARGUMENT_VALUE"
    text = context(rule.format(v=marker), layer)
    result, findings = redact_text(text)
    assert marker not in result, (text, result, findings)
    assert findings
    assert redact_text(result)[0] == result


@pytest.mark.parametrize("layer", ["0", "1", "2", "3", "shell", "percent1", "percent2"])
def test_r42_exempt_call_stops_at_own_parenthesis(layer):
    text = context('token = os.getenv("TOKEN")\nprint("hello")', layer)
    assert redact_text(text) == (text, [])


@pytest.mark.parametrize("special", [",", "}"])
def test_r42_punctuation_inside_string_is_not_outer_structure(special):
    marker = f"{'QUOTE'}_MARKER_STRING_SUFFIX"
    text = json.dumps(json.dumps({"note": "password=first" + special + marker}))
    result, findings = redact_text(text)
    assert marker not in result and findings
