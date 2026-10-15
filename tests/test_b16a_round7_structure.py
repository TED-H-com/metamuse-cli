"""Delimiter ownership and layer-true exemption regressions (synthetic only)."""

import pytest
from b16a_r7_probes import ROWS

from metamuse.safety import redact_text

V = f"{'R7'}_MARKER_SYNTHETIC_643829"


@pytest.mark.parametrize("name,text,markers", ROWS, ids=[r[0] for r in ROWS])
def test_r7_review_probes(name, text, markers):
    out, found = redact_text(text)
    assert all(marker not in out for marker in markers), (name, text, out, found)
    assert redact_text(out)[0] == out


@pytest.mark.parametrize("prefix", ["", "the users' ", "r'\\' ", '"unclosed ', "'x' ", '"x" '])
@pytest.mark.parametrize(
    "shape",
    [
        "password=pa'-{v}",
        "sid=pa'-{v}; theme=dark",
        "'https://alice:pa]-{v}@example.invalid/p'",
        '"https://alice:pa\'-{v}@example.invalid/p"',
        "'https://example.invalid/p?token=pa]-{v}'",
        'cu' + 'rl ' + "-u alice:pa'-{v} https://example.invalid",
        "sshpass -p pa'-{v} ssh host",
    ],
)
def test_r7_owned_delimiters_ignore_unrelated_pairing(prefix, shape):
    text = prefix + shape.format(v=V)
    out, found = redact_text(text)
    assert V not in out and found, (text, out, found)


@pytest.mark.parametrize(
    "shape",
    [
        "password=<{v}%3e",
        "password=%3c{v}>",
        "password={{{v}%7d",
        "password=%7b{v}}}",
        "password=%{v}%25",
        "https://alice:${{{v}%7d@example.invalid/p",
        "https://alice:{{{{{v}%7d%7d@example.invalid/p",
        'https://alice:os.getenv("{v}"%29@example.invalid/p',
        "https://example.invalid/p?token=<{v}%3e",
        "<password><{v}%3e</password>",
        '<input name="password" value="<{v}%3e">',
    ],
)
def test_r7_mixed_layer_exemptions_private(shape):
    text = shape.format(v=V)
    assert V not in redact_text(text)[0], (text, redact_text(text))


@pytest.mark.parametrize(
    "text",
    [
        'export API_KEY="${API_KEY:-${FALLBACK_KEY}}"',
        "password=${PASSWORD:-$FALLBACK}",
        "token=${TOKEN:-}",
        'cu' + 'rl ' + '-u "alice:${PASS}" https://example.invalid',
        "Set password=${PW}.",
        "Use `password=${PW}`.",
        "Why did git checkout -b main fail?",
        "Run python -b to check BytesWarning.",
        "Which record is identified by that UUID?",
        "mktime()-based conversion",
        "len(a)-by-len(b) array",
        "# filesets are identified by four decimal values",
    ],
)
def test_r7_benign_anchors_and_defaults(text):
    assert redact_text(text) == (text, [])


@pytest.mark.parametrize(
    "shape",
    [
        'cu' + 'rl -b "${{C:--u alice:{v}}}"',
        'cu' + 'rl -b "${{C:---user alice:{v}}}"',
        'cmd="${{CMD:---password={v}}}"',
        "password=${{PASSWORD:-{v}}}",
        "password='pa'\"'\"'{v}'",
        'cu' + 'rl ' + "-u 'alice:pa'\\''{v}' https://example.invalid",
        '"password=pa"" {v}"',
        "password = “{v} {v}”",
        "https://example.invalid/p?sp=${{SP:-?skoid={v}}}",
        'cu' + 'rl -b "${{C:- -u alice:{v}}}"',
        'sshpass -p "${{P:-sshpass -p {v}}}"',
    ],
)
def test_r7_shell_operands_quotes_and_reentry(shape):
    text = shape.format(v=V)
    out, found = redact_text(text)
    assert V not in out and found, (text, out, found)
    assert redact_text(out)[0] == out


@pytest.mark.parametrize(
    "follower", list("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~") + ["a", "1", " ", ""]
)
def test_r7_d2_follower_cut_is_exact(follower):
    text = "password='pa'" + follower + V + "'"
    # Runs of closing characters can precede a permitted follower. This row
    # deliberately puts a marker immediately after the follower, so only
    # whitespace/comma/semicolon/ampersand validate the internal closer.
    expected = "password='[REDACTED]'" + (
        follower + V + "'" if follower in {" ", ",", ";", "&"} else ""
    )
    assert redact_text(text)[0] == expected, (text, redact_text(text), expected)


@pytest.mark.parametrize(
    "text",
    [
        "password=${PW}.",
        "password=${PW}!!!",
        "password=${PW})",
        "password=[REDACTED])",
        'password="[REDACTED]"',
    ],
)
def test_r7_d3_exemption_uses_trimmed_whole_value(text):
    assert redact_text(text) == (text, [])


@pytest.mark.parametrize(
    "shape",
    [
        "password='pa'' {v}'",
        'password="pa\\" {v}"',
        'password="pa`" {v}"',
        "password='pa'\"'\"'{v}'",
        "password='pa'\\''{v}'",
        "password=“pa”~{v}”",
        "password=‘pa’~{v}’",
    ],
)
def test_r7_d4_quote_forms_never_expose_tail(shape):
    text = shape.format(v=V)
    assert V not in redact_text(text)[0], (text, redact_text(text))


@pytest.mark.parametrize("prefix", ["(", "[", "{", "<"])
@pytest.mark.parametrize(
    "shape",
    [
        'password="pa) {v}"',
        'cu' + 'rl ' + "-u 'alice:pa] {v}' https://example.invalid",
        "sshpass -p 'pa}} {v}' ssh host",
    ],
)
def test_r7_innermost_value_quote_owns_extent(prefix, shape):
    text = prefix + shape.format(v=V)
    assert V not in redact_text(text)[0], (text, redact_text(text))


@pytest.mark.parametrize("header", ["|2-", "|-2", ">2", "| # note", "# note"])
def test_r7_yaml_header_owns_deeper_body(header):
    text = "password: " + header + "\n  " + V + "\nother: public"
    out, found = redact_text(text)
    assert V not in out and found, (text, out, found)


@pytest.mark.parametrize(
    "op",
    [":-", ":=", ":+", "-", "=", "+", "#", "##", "%", "%%", "/", "//", "^", "^^", ",", ",,", ":2"],
)
def test_r7_shell_literal_operand_private(op):
    text = "password=${PASSWORD" + op + V + "}"
    assert V not in redact_text(text)[0], (text, redact_text(text))


@pytest.mark.parametrize(
    "text",
    [
        "password=${PASSWORD:?required}",
        "password=${PASSWORD?required}",
        "password=${PASSWORD:-${FALLBACK}}",
        "password=${PASSWORD:-$FALLBACK}",
        "password=${PASSWORD:-}",
    ],
)
def test_r7_shell_reference_only_defaults(text):
    assert redact_text(text) == (text, [])


@pytest.mark.parametrize(
    "shape", ["<https://alice:pa>{v}@example.invalid/p", "<https://example.invalid/p?token=pa>{v}"]
)
def test_r7_autolink_invalid_follower_is_data(shape):
    text = shape.format(v=V)
    assert V not in redact_text(text)[0], (text, redact_text(text))


@pytest.mark.parametrize(
    "text", ['cu' + 'rl ' + '-u alice:${PASS}', 'cu' + 'rl ' + '-u alice:[REDACTED]', 'cu' + 'rl ' + '-u "alice:${PASS}"']
)
def test_r7_curl_exemption_decides_password_without_username(text):
    assert redact_text(text) == (text, [])


def test_r7_heredoc_header_never_swallows_form_markup():
    from urllib.parse import quote_plus

    text = (
        "cat <<EOF\n# operator's note\n q=" + quote_plus("<password>" + V + "</password>") + "\nEOF"
    )
    assert V not in redact_text(text)[0], (text, redact_text(text))


def test_r7_unquoted_nested_tag_starts_independent_context():
    text = "<div q=<password>" + V + "</password>"
    assert V not in redact_text(text)[0], (text, redact_text(text))


@pytest.mark.parametrize(
    "shape", ["(Cookie: 'pa) {v}')", "[https://example.invalid/p?token='pa] {v}']"]
)
def test_r7_innermost_header_and_query_quote(shape):
    text = shape.format(v=V)
    assert V not in redact_text(text)[0], (text, redact_text(text))


@pytest.mark.parametrize("value", ["${PASS}", "<EXAMPLE>", "[REDACTED]", 'os.getenv("PASS")'])
def test_r7_every_scanner_shares_closed_exemptions(value):
    import json

    templates = [
        "password={v}",
        "Cookie: {v}",
        "<password>{v}</password>",
        "https://alice:{v}@example.invalid/p",
        "https://example.invalid/p?token={v}",
        'cu' + 'rl ' + '-u alice:{v}',
        "sshpass -p {v}",
    ]
    texts = [t.format(v=value) for t in templates]
    texts.append(json.dumps({"name": "password", "value": value}))
    for text in texts:
        assert redact_text(text) == (text, []), (text, redact_text(text))


@pytest.mark.parametrize(
    "value", ["<EXAMPLE%3e", "%3cEXAMPLE>", "${PASS%7d", 'os.getenv("PASS"%29']
)
def test_r7_har_exemption_is_layer_true(value):
    import json

    text = json.dumps({"name": "password", "value": value})
    assert redact_text(text)[0] != text, (text, redact_text(text))


@pytest.mark.parametrize(
    "text", ["Use `token=<your token>`.", "password=<your token>", 'cu' + 'rl ' + '-u "alice:<your token>"']
)
def test_r7_complete_placeholder_with_spaces(text):
    assert redact_text(text) == (text, []), (text, redact_text(text))


@pytest.mark.parametrize(
    "shape", ["[ password=pa],{v}", "[ ' https://alice:pa],{v}@example.invalid/p"]
)
def test_r7_whitespace_breaks_opener_ownership(shape):
    text = shape.format(v=V)
    assert V not in redact_text(text)[0], (text, redact_text(text))


def test_r7_typographic_quote_ends_complete_placeholder():
    text = "password=“${PW}” next=public"
    assert redact_text(text) == (text, [])


def test_r7_comment_context_never_consumes_following_public_text():
    text = "<!-- <password>" + V + " --> PUBLIC_AFTER"
    out, found = redact_text(text)
    assert V not in out and out.endswith("--> PUBLIC_AFTER"), (text, out, found)


def test_r7_yaml_header_inside_earlier_comment_still_owns_body():
    text = "prefix:# note api_key: |\n  " + V + "\nnext: public"
    assert V not in redact_text(text)[0], (text, redact_text(text))


@pytest.mark.parametrize("prefix", ["(", "the users' (", "func(", "r'\\' ("])
def test_r7_unowned_call_pairing_never_hides_cookie_chain(prefix):
    text = prefix + "theme=dark; foo=" + V + ")"
    assert V not in redact_text(text)[0]


def test_r7_curl_argument_cannot_hide_simple_command_separator():
    text = 'cu' + 'rl -b private; git -b public'
    assert redact_text(text)[0] == 'cu' + 'rl -b [REDACTED]; git -b public'


@pytest.mark.parametrize("tail", ["a", "1", "-"])
def test_r7_sentinel_prefix_with_short_tail_is_not_exempt(tail):
    assert redact_text("Bearer [REDACTED]" + tail)[0] == "Bearer [REDACTED]"


@pytest.mark.parametrize("template", ["Cookie: '{v}", 'cu' + 'rl ' + "-u 'alice:{v}", "--password '{v}"])
def test_r7_unclosed_value_quote_is_not_empty(template):
    assert V not in redact_text(template.format(v=V))[0]


@pytest.mark.parametrize("template", ["password='pa\\' {v}'", "password=`pa\\` {v}`"])
def test_r7_backslash_quote_is_data_without_json_escape_decoding(template):
    assert V not in redact_text(template.format(v=V))[0]


def test_r7_deeper_newline_cannot_shorten_encoded_header():
    from urllib.parse import quote

    text = quote("Cookie: " + "a" * 96 + "\\n" + V, safe="")
    assert V not in redact_text(text)[0], (text, redact_text(text))


def test_r7_deeper_quote_cannot_shorten_encoded_assignment():
    from urllib.parse import quote

    text = quote('password="pa\\" ' + V + '"', safe="")
    assert V not in redact_text(text)[0], (text, redact_text(text))


@pytest.mark.parametrize(
    "shape",
    [
        '<input value="{v}" name=password/>',
        '<input type=" password " value="{v}">',
        "<input name=`password` value=`{v}`>",
        "<!-- <password user's tag> -->\n<password>{v}</password>",
        "<password label='bob\\'s'>{v}</password>",
        '<input name=password value=pa"<{v}>',
    ],
)
def test_r7_markup_per_tag_contract(shape):
    assert V not in redact_text(shape.format(v=V))[0]


@pytest.mark.parametrize("final", ["Private-MAC:", "Private-Hash:"])
def test_r7_private_block_owns_its_final_line(final):
    text = (
        "Pu"
        + "TTY-User-Key-File-3: ssh-rsa\nComment: ] ' prod\nPrivate-Lines: 1\n"
        + V
        + "\n"
        + final
        + " public"
    )
    assert V not in redact_text(text)[0]


def test_r7_form_body_continues_through_markup_quotes():
    text = 'Log: body=<input+name="password"+value="' + V + '">'
    assert V not in redact_text(text)[0]


@pytest.mark.parametrize("template", ["password='pa'\"'\"' {v}'", "password='pa'\\'' {v}'"])
def test_r7_shell_idiom_space_is_still_value_data(template):
    assert V not in redact_text(template.format(v=V))[0]


@pytest.mark.parametrize("context", ["It's failing: ", "<!-- "])
def test_r7_json_line_escape_follows_an_owned_literal(context):
    import json

    text = json.dumps({"note": context + "\n'" + 'token=os.getenv("TOKEN")' + "'\n" + context})
    assert redact_text(text) == (text, [])


def test_r7_url_candidate_cannot_claim_next_json_fields_colon():
    text = '{"url":"https://example.invalid","email":"alice@example.invalid"}'
    assert redact_text(text) == (text, [])


@pytest.mark.parametrize("value", ["value]", "${PW}.", "[REDACTED].", "value.,;"])
def test_r7_d3_exemption_never_trims_an_own_quoted_value(value):
    assert redact_text('password="' + value + '"')[0] == 'password="[REDACTED]"'


def test_r7_mixed_layer_userinfo_with_encoded_operator_is_private():
    text = "https://alice%3a<" + V + "%3e@example.invalid/p"
    assert V not in redact_text(text)[0]


def test_r7_raw_bearer_preserves_overlapping_finding_class():
    value = "s" + "k-" + "abcdefgh" + str(12) + "/a/bcAuthorization"
    assert redact_text("Bearer " + value + ": ") == (
        "Bearer [REDACTED] ",
        [{"kind": "bearer_token", "count": 1}],
    )


def test_r7_query_enters_exempt_shell_error_message():
    text = "https://example.invalid/p?sp=${SP:??skoid=" + V + "}"
    assert V not in redact_text(text)[0]


def test_r7_own_single_quote_never_consumes_following_line():
    text = "password='" + V + "\npublic line'\nend"
    assert redact_text(text)[0] == "password='[REDACTED]\npublic line'\nend"


@pytest.mark.parametrize("quote", ['"' * 3, "'" * 3])
def test_r7_triple_quote_keeps_complete_placeholder(quote):
    text = "password=" + quote + "${PW}" + quote
    assert redact_text(text) == (text, [])


@pytest.mark.parametrize(
    "shape",
    [
        'cu' + 'rl -b "${{C:? -u alice:{v}}}"',
        'sshpass -p "${{P:?sshpass -p {v}}}"',
    ],
)
def test_r7_flag_scanners_enter_exempt_shell_error_messages(shape):
    text = shape.format(v=V)
    assert V not in redact_text(text)[0]
