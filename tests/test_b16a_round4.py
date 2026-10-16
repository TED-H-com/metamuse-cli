"""Round-4 privacy regressions; all credential values are synthesized at runtime."""

import json
from urllib.parse import quote

import pytest

from metamuse import safety
from metamuse._muse_lifecycle import _check_question
from metamuse.models import CoreError, ErrorCode


def marker(prefix="round4"):
    return f"{prefix}_MARKER_SYNTHETIC_ONLY"


def assert_private(text, value):
    output, findings = safety.redact_text(text)
    assert any(row["kind"] != "local_path" for row in findings), (text, output)
    assert value not in output, (text, output)
    assert not any(value[i : i + 6] in output for i in range(len(value) - 5)), output
    assert safety.redact_text(output) == (output, [])
    with pytest.raises(CoreError) as refused:
        _check_question(text)
    assert refused.value.detail.code == ErrorCode.INPUT_INVALID
    assert value not in str(refused.value)
    return output


@pytest.mark.parametrize("name", ["password", "token", "DB_PASSWORD"])
def test_r4_short_values_are_credentials(name):
    value = f"{'A'}{3}!"
    assert_private(f"{name}={value}", value)


def test_r4_prose_values_after_sensitive_names_are_credentials():
    value = f"{'Two'} {'Words'}{42}!"
    assert_private('password="' + value + '"', value)


def test_r4_bearer_closure_covers_intersecting_display_masks():
    text = "Bearer /home/" + "synthetic" + "/file;other"
    assert safety.redact_text(text) == (
        "Bearer [REDACTED]",
        [{"kind": "local_path", "count": 1}],
    )


@pytest.mark.parametrize(
    "nested", ["password:", "password=", "--password", "-password", "password ="]
)
@pytest.mark.parametrize("space", [" ", " \n"])
def test_r4_consumed_assignment_covers_nested_value_after_whitespace(nested, space):
    value = marker()
    assert_private('"token": +https://example.invalid/' + nested + space + value, value)


def test_r4_malformed_preceding_line_does_not_hide_quoted_value():
    value = marker()
    assert_private('log: {"password": "unfinished\n{"x-api-key":"' + value + '"}', value)


@pytest.mark.parametrize("prefix", ["&", "?", "#", "%26", "%2526"])
def test_r4_colon_assignment_after_query_like_separator(prefix):
    value = marker()
    assert_private("note" + prefix + "secret: " + value, value)


def test_r4_literal_percent_does_not_hide_a_named_assignment():
    value = marker()
    assert_private("sig?%3D%25token%3D" + value, value)


@pytest.mark.parametrize(
    "text", ["prompt='Password: '", "def getpass(prompt='Password: ', stream=None):"]
)
def test_r4_empty_value_before_enclosing_quote_is_exempt(text):
    assert safety.redact_text(text) == (text, [])
    _check_question(text)


def test_r4_query_sentinel_is_only_exempt_as_a_whole_value():
    value = marker()
    assert_private("https://example.invalid/?token=[REDACTED]" + value + "&format=json", value)


def test_r4_triple_quoted_value_preserves_following_fields():
    value = marker()
    text = 'password = """first\n' + value + '"""\nnext=1'
    output = assert_private(text, value)
    assert output == 'password = """[REDACTED]"""\nnext=1'


@pytest.mark.parametrize("name", ["Authorization", "Proxy-Authorization", "Cookie"])
@pytest.mark.parametrize("newline", ["%0A", "%250A", r"\n", r"\u000a"])
def test_r31_header_decoded_newline_is_value_data(name, newline):
    value = marker()
    assert_private(f"{name}: Basic prefix{newline}{value}", value)


@pytest.mark.parametrize("name", ["Authorization", "Proxy-Authorization", "Cookie"])
def test_r31_nested_header_newline_layer(name):
    value = marker()
    text = "q=" + quote(f"{name}: Basic first%0A{value}", safe="")
    assert_private(text, value)


@pytest.mark.parametrize("operator", ["=", ": "])
@pytest.mark.parametrize("delimiters", [("[", "]"), ("{", "}")])
def test_r31_container_closer_respects_decoding_layer(operator, delimiters):
    value = marker()
    opening, closing = delimiters
    inner = "password" + operator + opening + "first" + quote(closing) + " " + value + closing
    assert_private(quote(inner, safe=""), value)


@pytest.mark.parametrize("kind", ["yaml", "yaml_block", "xml", "private_key", "folded_header"])
def test_r31_deeper_terminators_in_multiline_and_block_syntax(kind):
    value = marker()
    if kind == "yaml":
        text = "password: first%0A" + value
    elif kind == "yaml_block":
        text = "password: |\n  first%0A" + value + "\n  second\nnext=1"
    elif kind == "xml":
        text = "<password>first" + quote("</password>", safe="") + value + "</password>"
    elif kind == "private_key":
        begin = "-----BE" + "GIN PRIVATE KEY-----"
        end = "-----E" + "ND PRIVATE KEY-----"
        text = begin + "\nfirst" + quote(end, safe="") + value + "\n" + end
    else:
        text = "Cookie: first\n continuation%0A" + value
    assert_private(quote(text, safe=""), value)


@pytest.mark.parametrize("escape", ["22", "27", "20", "2C", "3B", "5D", "7D"])
@pytest.mark.parametrize("position", ["first", "middle", "field"])
def test_r31_encoded_terminators_are_value_data(escape, position):
    value = marker()
    body = f"password={'Ab3' if position != 'first' else ''}%{escape}{value}&user=bob"
    if position == "field":
        body = "username=bob&" + body
    assert_private(body, value)


@pytest.mark.parametrize(
    "template",
    [
        "client_secret=Ab3%3B{v}&grant_type=client_credentials",
        "curl -d 'password=Ab3%2C{v}&user=bob' https://example.invalid/login",
        'HAR {{"postData": {{"text": "password=%3B{v}&user=bob"}}}}',
        "password=It%27s+{v}&username=bob",
        "q=password%3Dabc%26user%3Dbob",
    ],
)
def test_r31_form_carriers(template):
    value = marker()
    if "{v}" in template:
        assert_private(template.format(v=value), value)
    else:
        output, findings = safety.redact_text(template)
        assert "abc" not in output and "user%3Dbob" in output and findings


@pytest.mark.parametrize("layers", [1, 2, 3])
def test_r31_decoding_layer_terminators(layers):
    value = marker()
    text = f"password=Ab3;{value}&user=bob"
    for _ in range(layers):
        text = quote(text, safe="")
    # The same-layer ';' terminates this inner form, unlike a deeper escape.
    output, findings = safety.redact_text(text)
    assert findings and "Ab3" not in output and value in output


def test_r31_raw_span_is_not_dropped_for_percent():
    value = marker()
    assert_private(f"password=Ab3%3B{value}", value)


@pytest.mark.parametrize("escape", ["22", "27", "20", "2C", "3B", "5D", "7D"])
@pytest.mark.parametrize("layers", [1, 2])
def test_r31_operator_layer_relative_to_value_delimiters(escape, layers):
    value = marker()
    text = f"password=Ab3%{escape}{value}&user=bob"
    for _ in range(layers):
        text = quote(text, safe="")
    assert_private(text, value)


@pytest.mark.parametrize("kind", ["assignment", "bearer"])
def test_r31_r32_raw_spans_are_independent_of_decoded_rules(monkeypatch, kind):
    value = marker()
    text = f"password=Ab3%3B{value}" if kind == "assignment" else f"Is this valid? Bearer {value}"
    monkeypatch.setattr(safety, "_view_spans", lambda *args: iter(()))
    assert_private(text, value)


@pytest.mark.parametrize(
    "template",
    [
        "q=password%3A+%22{v}%22",
        "q=%7B%22password%22%3A+%22{v}%22%7D",
        '{{"cfg": "password: |\\n  {v}\\n"}}',
        "q=--password+{v}",
        "q=curl+-u+user%3A{v}",
        "https://example.invalid/run?q=--password+{v}",
    ],
)
def test_r33_partial_mask_table(template):
    value = marker()
    output = assert_private(template.format(v=value), value)
    assert safety.redact_text(output) == (output, [])


@pytest.mark.parametrize("changes", [0, 1, 2, 3, 4, 5, 9])
def test_r33_exact_internal_application_bound(monkeypatch, changes):
    calls = 0

    def single(text):
        nonlocal calls
        calls += 1
        return (
            (text + "!", [{"kind": "session_cookie", "count": 1}])
            if calls <= changes
            else (text, [])
        )

    monkeypatch.setattr(safety, "_redact_once", single, raising=False)
    output, findings = safety.redact_text("input")
    assert calls == min(changes + 1, 4)
    assert any(row["kind"] == "redaction_unstable" for row in findings) == (changes >= 4)
    assert output == ("[REDACTED]" if changes >= 4 else "input" + "!" * changes)


@pytest.mark.parametrize("value", ["text", {"text": "value"}, ["text"]])
def test_r33_unstable_bundle_values_refuse(monkeypatch, value):
    from metamuse.content import _safe_value

    monkeypatch.setattr(safety, "_redact_once", lambda text: (text + "!", []))
    with pytest.raises(CoreError, match="redaction did not stabilize"):
        _safe_value(value, [])


def test_r33_nonrefusable_display_masks_unstable_value(monkeypatch):
    value = marker()
    monkeypatch.setattr(
        safety,
        "_redact_once",
        lambda text: (text + "!", []) if text.startswith(value) else (text, []),
    )
    assert safety.redact_display(value) == "[REDACTED]"
    from metamuse._muse_redact import sanitize_document
    from metamuse.muse import _Secrets

    assert sanitize_document({"note": value}) == {"note": "[REDACTED]"}
    assert _Secrets().text(value) == "[REDACTED]"
    with pytest.raises(CoreError) as error:
        _check_question(value)
    assert error.value.detail.code == ErrorCode.INPUT_INVALID


def test_r33_nonrefusable_display_masks_unstable_key(monkeypatch):
    key = marker()
    monkeypatch.setattr(
        safety,
        "_redact_once",
        lambda text: (text + "!", []) if text.startswith(key) else (text, []),
    )
    from metamuse._muse_redact import sanitize_document

    rendered = sanitize_document({key: "public value"})
    assert rendered == {"[REDACTED]": "public value"}
    assert key not in json.dumps(rendered)
