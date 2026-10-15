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


@pytest.mark.parametrize("wrapper", ['"""{x}"""', "'''{x}'''", "`{x}`"])
def test_r6_multiline_tokens_preserve_benign_content(wrapper):
    text = wrapper.replace("{x}", 'token = os.getenv("TOKEN")\nx = 1')
    assert redact_text(text) == (text, [])


@pytest.mark.parametrize("kind", ["name", "type", "meta", "nested"])
@pytest.mark.parametrize("layers", [0, 1, 2])
def test_r6_html_attributes_have_complete_local_extents(kind, layers):
    if kind == "nested":
        text = (
            "<input name=\"password\" value=\"${T:-<input name='password' value='"
            + MARKER
            + "'>}\">"
        )
    else:
        attr = (
            'name="password"'
            if kind == "name"
            else 'type="password"'
            if kind == "type"
            else 'name="csrf_token"'
        )
        value = "content" if kind == "meta" else "value"
        text = "<input " + value + '="public\n' + MARKER + '" ' + attr + ">"
    output, findings = redact_text(encoded(text, layers))
    assert MARKER not in output and findings


def test_r6_full_sweep_counterexamples():
    from b16a_r6_counterexamples import TEMPLATES

    first = f"{'SYNTH'}qheadqVAL{4242}"
    tail = f"{'SYNTH'}qtailqVAL{4242}"
    value = f"{'CONTEXT'}_PRIVATE_MARKER_{684329}"
    for template in TEMPLATES:
        text = (
            template.replace("__HEAD__", first)
            .replace("__TAIL__", tail)
            .replace("__VALUE__", value)
        )
        output, findings = redact_text(text)
        assert not any(marker in output for marker in [first, tail, value]), (
            text,
            output,
            findings,
        )
        assert redact_text(output)[0] == output


@pytest.mark.parametrize("element", ["password", "wsse:Password"])
@pytest.mark.parametrize("layers", [0, 1, 2])
def test_r6_multiline_xml_attributes_do_not_hide_body(element, layers):
    text = "<" + element + ' comment="ordinary\ntext">' + MARKER + "</" + element + ">"
    assert MARKER not in redact_text(encoded(text, layers))[0]


@pytest.mark.parametrize("attribute", ["name", "type"])
def test_r6_html_character_references_name_the_private_field(attribute):
    text = "<input " + attribute + '="pass&#119;ord" value="' + MARKER + '">'
    assert MARKER not in redact_text(text)[0]


def test_r6_decoded_quote_cannot_shorten_shallower_value():
    from metamuse.safety import _decode_view, _decoding_depths, _Syntax

    text = "password=prefix" + MARKER + "%22inner%22&x=public"
    view, edits = _decode_view(text)
    syntax = _Syntax(view, _decoding_depths(len(text), [edits]))
    assert syntax.value(view, len("password="), operator_depth=0) == len(view)
    assert MARKER not in redact_text(text)[0]


def test_r6_deeper_close_does_not_force_shallower_pending_pair():
    from metamuse.safety import _Syntax

    text = "\"outer 'inner\" tail'"
    depths = bytearray(len(text))
    depths[0] = depths[text.index('"', 1)] = 1
    syntax = _Syntax(text, depths)
    opening = text.index("'")
    assert syntax.ends.get(opening) == len(text)
    assert syntax.quote_closings.get(len(text) - 1) == opening


def test_r6_har_does_not_resynchronize_within_failed_candidate():
    marker = f"{'HARSTATE'}_MARKER_PRIVATE_432198"
    text = '{"name":"sid", ,"value":"' + marker + '"}'
    from metamuse.safety import _har_json_spans

    assert list(_har_json_spans(text)) == []


def test_r6_quote_opener_preserves_following_public_url():
    text = 'stray " curl ' + '-u "alice:my pass ' + MARKER + '" https://example.invalid/a'
    output, findings = redact_text(text)
    assert MARKER not in output and findings
    assert "https://example.invalid/a" in output


def test_r6_putty_version_three_is_private():
    text = "Pu" + "TTY-User-Key-File-3: ssh-rsa\nPrivate-Lines: 1\n" + MARKER + "\nPrivate-MAC: 00"
    assert redact_text(text)[0] == "[REDACTED]"


@pytest.mark.parametrize("bad", ["!", "\u00a0"])
def test_r6_har_lexer_keeps_its_first_error(bad):
    from metamuse.safety import _har_json_spans

    text = '{"name":"sid",' + bad + '"value":"' + MARKER + '"}'
    assert list(_har_json_spans(text)) == []


@pytest.mark.parametrize("prefix", ["", "存取網址：", "Here's the link: "])
@pytest.mark.parametrize("terminator", ["&format=json", "#overview", " next"])
def test_r6_query_terminators_preserve_public_suffix(prefix, terminator):
    marker = f"{'QUERY'}_PRIVATE_MARKER_725319"
    text = prefix + "https://example.org/?access_token=" + marker + terminator
    output, findings = redact_text(text)
    assert output == prefix + "https://example.org/?access_token=[REDACTED]" + terminator
    assert findings == [{"kind": "url_token", "count": 1}]
    assert redact_text(output)[0] == output


def test_r6_deeper_quote_never_pairs_shallower_opener():
    from metamuse.safety import _Syntax

    text = '"outer"'
    depths = bytearray(len(text))
    depths[-1] = 1
    syntax = _Syntax(text, depths)
    assert syntax.quote_closings.get(len(text) - 1) is None


def test_r6_quoted_punctuation_does_not_close_outer_structure():
    from metamuse.safety import _Syntax

    text = '(")")'
    syntax = _Syntax(text)
    assert syntax.closings.get(len(text) - 1) == 0
    assert syntax.closings.get(2) is None


def test_r6_har_recovers_candidate_swallowed_by_invalid_string():
    from metamuse.safety import _har_json_spans

    marker = f"{'RECOVERY'}_MARKER_PRIVATE_813629"
    text = '{"broken":"prefix {"name":"sid","value":"' + marker + '"}'
    spans = list(_har_json_spans(text))
    assert any(marker in text[start:end] for start, end, *_ in spans)


def test_r6_nested_form_prefix_has_its_own_decoding_layer():
    from metamuse.safety import _layered_form_ranges

    text = "q=token=private"
    depths = bytearray(len(text))
    depths[7] = 1
    assert (2, len(text), 1) in list(_layered_form_ranges(text, depths, [(0, len(text))]))


@pytest.mark.parametrize("layers", [0, 1, 2])
@pytest.mark.parametrize("key", ["password", "token", "cookie", "authorization"])
def test_r6_quoted_argument_can_overlap_a_private_assignment(key, layers):
    text = '--password "' + key + '": ' + MARKER
    output, findings = redact_text(encoded(text, layers))
    assert MARKER not in output and findings
    assert redact_text(output)[0] == output


@pytest.mark.parametrize("layers", [1, 2, 3])
@pytest.mark.parametrize("prefix", ["", "Here's the URL: ", "note `\n"])
def test_r6_encoding_the_whole_url_keeps_literal_question_in_value(layers, prefix):
    from urllib.parse import quote

    text = "https://example.invalid/?token=" + MARKER + "?" + MARKER + "&view=1"
    for _ in range(layers):
        text = quote(text, safe="")
    output, findings = redact_text(prefix + text)
    assert MARKER not in output and findings
    assert redact_text(output)[0] == output


@pytest.mark.parametrize("layers", [0, 1, 2])
def test_r6_complete_template_expression_remains_exempt(layers):
    text = encoded("password: ${{ secrets.DB_PASSWORD }}", layers)
    assert redact_text(text) == (text, [])


def test_r6_form_har_does_not_spend_json_decode_budget():
    from urllib.parse import quote

    from metamuse.safety import _har_candidates

    text = "q=" + quote(json.dumps({"cookies": [{"name": "theme", "value": MARKER}]}), safe="")
    spans = list(_har_candidates(text, _depth=3, _percent_depth=3))
    assert any(MARKER in text[start:end] for start, end, *_ in spans)


def test_r6_percent_har_is_independent_of_unrelated_json_candidate():
    from urllib.parse import quote

    from metamuse.safety import _har_candidates

    text = 'noise {\\"bad\\" ' + quote(
        json.dumps({"cookies": [{"name": "theme", "value": MARKER}]}), safe=""
    )
    spans = list(_har_candidates(text, _depth=3))
    assert any(MARKER in text[start:end] for start, end, *_ in spans)


@pytest.mark.parametrize("header", ["Cookie", "Set-Cookie", "Authorization", "Proxy-Authorization"])
@pytest.mark.parametrize("quote", ['"', "'", "`"])
@pytest.mark.parametrize("layers", [0, 1, 2])
def test_r6_header_mask_prefix_does_not_prove_a_close(header, quote, layers):
    text = encoded(header + ": [REDACTED]" + quote + MARKER, layers)
    output, findings = redact_text(text)
    assert MARKER not in output and findings
    assert redact_text(output)[0] == output
