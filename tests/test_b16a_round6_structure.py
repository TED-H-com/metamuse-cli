"""Ambiguous surrounding text cannot shorten a credential value."""

import json
from urllib.parse import quote

import pytest

from metamuse.safety import redact_text

MARKER = f"{'STRUCTURE'}_MARKER_PRIVATE_643829"
CONTEXTS = (
    "",
    "Here's what I ran: ",
    "don't run ",
    'Note "unfinished: ',
    "# user's note\n",
    'doc = """{x}"""',
    "doc = '''{x}'''",
    "`{x}`",
)
COMMANDS = (
    "curl " + "-u 'alice:my pass {v}' https://example.invalid",
    'curl --cookie "my pass {v}"',
    'sshpass -p "my pass {v}" ssh host',
    'mysql -p"my pass {v}" db',
    'CREATE USER u IDENTIFIED BY "my pass {v}"',
    "curl --user `alice:my pass {v}`",
)


def encoded(text, layers):
    for _ in range(layers):
        text = json.dumps({"code": text})
    return text


@pytest.mark.parametrize("context", CONTEXTS)
@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize("layers", [0, 1, 2])
def test_r6_value_start_quote_opens(context, command, layers):
    value = command.format(v=MARKER)
    text = context.replace("{x}", value) if "{x}" in context else context + value
    text = encoded(text, layers)
    output, findings = redact_text(text)
    assert MARKER not in output and findings, (text, output, findings)
    assert redact_text(output)[0] == output


@pytest.mark.parametrize(
    "value",
    ["${{v}", "os.getenv({v}", "os.environ[{v}", "{{{{{v}", "<{v}", "%VAR{v}", "Optional[{v}"],
)
@pytest.mark.parametrize(
    "wrapper",
    [
        "password={x}",
        'password="{x}"',
        "password='{x}'",
        '{{"password":"{x}"}}',
        "password: {x}",
        "<password>{x}</password>",
    ],
)
@pytest.mark.parametrize("layers", [0, 1, 2])
def test_r6_unclosed_exemption_is_private(value, wrapper, layers):
    text = encoded(wrapper.replace("{x}", value.replace("{v}", MARKER)), layers)
    output, findings = redact_text(text)
    assert MARKER not in output and findings, (text, output)


@pytest.mark.parametrize("value", ['os.getenv("TOKEN_)")', 'os.environ["KEY[0]"]', "${A:-${B}}"])
@pytest.mark.parametrize("context", CONTEXTS)
def test_r6_complete_exemption_preserved(value, context):
    text = "token=" + value
    text = context.replace("{x}", text) if "{x}" in context else context + text
    assert redact_text(text) == (text, [])


@pytest.mark.parametrize(
    "shape",
    [
        'cookie = os.getenv("COOKIE", "set_cookie={v}")',
        'authorization = os.getenv("AUTH", "cookie={v}")',
        "Authorization: <cookie={v}>",
        "Cookie: ${{COOKIE:- set_cookie={v}}}",
        "<token>${{T:-<password>{v}</password>}}</token>",
        "<token>{{{{<password>{v}</password>}}}}</token>",
    ],
)
@pytest.mark.parametrize("layers", [0, 1, 2])
def test_r6_every_scanner_enters_exempt_value(shape, layers):
    text = encoded(shape.format(v=MARKER), layers)
    output, findings = redact_text(text)
    assert MARKER not in output and findings, (text, output)


@pytest.mark.parametrize(
    "text",
    [
        "//alice:{v}@example.invalid/p",
        "https://example.invalid,//alice:{v}@example.invalid/p",
        "[# don't\n'https://alice:pa]{v}@example.invalid/p']",
    ],
)
def test_r6_protocol_relative_and_local_url(text):
    source = text.format(v=MARKER)
    assert MARKER not in redact_text(source)[0]


@pytest.mark.parametrize(
    "text",
    [
        "<https://example.invalid:8080>,<alice@example.invalid>",
        "<https://example.invalid/p?token=>",
        r"https://host:5432\nalice@example.invalid",
        r"ftp://host:5432\nalice@example.invalid",
    ],
)
def test_r6_url_benign_boundaries(text):
    assert redact_text(text) == (text, [])


@pytest.mark.parametrize(
    "shape",
    [
        '<input name="password" value="{v}">',
        '<meta content="{v}" name="csrf_token">',
        "<input value='{v}' type='password'>",
        "<input name=password value={v}>",
    ],
)
@pytest.mark.parametrize("layers", [0, 1, 2])
def test_r6_html_private_attributes(shape, layers):
    text = encoded(shape.format(v=MARKER), layers)
    assert MARKER not in redact_text(text)[0]


@pytest.mark.parametrize(
    "prefix",
    ["", "HAR capture: ", "```json\n", "Selected exact revisions only.\n", 'broken " { bogus: " '],
)
@pytest.mark.parametrize(
    "record",
    [
        '{{"name":"sid","value":"{v}","name":"theme"}}',
        '{{"name":"sid","value":"{v}","value":"{v}"}}',
        '{{"cookies":[{{"name":"theme","value":"{v}"}}]}}',
    ],
)
def test_r6_har_anywhere_repeated_names(prefix, record):
    text = prefix + record.format(v=MARKER)
    assert MARKER not in redact_text(text)[0], redact_text(text)


def test_r6_key_space_spellings():
    for name in ["OPENSSH PRIVATE KEY", "PGP PRIVATE KEY BLOCK"]:
        text = "-----BE" + f"GIN {name}-----\n{MARKER}\n-----E" + f"ND {name}-----"
        for value in [text, quote(text, safe="").replace("%20", "+")]:
            assert MARKER not in redact_text(value)[0]


def test_r6_pairing_is_local_and_contraction_aware():
    from metamuse.safety import _Syntax

    for prefix in ["don't ", "user's ", "stray '\n", "stray `\n", 'stray "\n']:
        text = prefix + "'value'"
        syntax = _Syntax(text)
        assert syntax.quote_closings.get(len(text) - 1) == len(prefix)
    text = '"""\n"value"\n"""'
    syntax = _Syntax(text)
    assert syntax.ends.get(0) == len(text)
    assert syntax.quote_closings.get(text.index('"', 5)) == 4


@pytest.mark.parametrize('wrapper', ['"""{x}"""', "'''{x}'''", '`{x}`'])
def test_r6_multiline_tokens_preserve_benign_content(wrapper):
    text = wrapper.replace('{x}', 'token = os.getenv("TOKEN")\nx = 1')
    assert redact_text(text) == (text, [])
