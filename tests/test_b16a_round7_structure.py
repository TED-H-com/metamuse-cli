"""Owned credential extent regressions (synthetic only)."""

import pytest

from metamuse.safety import redact_text

V = f"{'R7'}_MARKER_SYNTHETIC_643829"


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



@pytest.mark.parametrize(
    "shape", ["(Cookie: 'pa) {v}')", "[https://example.invalid/p?token='pa] {v}']"]
)
def test_r7_innermost_header_and_query_quote(shape):
    text = shape.format(v=V)
    assert V not in redact_text(text)[0], (text, redact_text(text))



@pytest.mark.parametrize(
    "shape", ["[ password=pa],{v}", "[ ' https://alice:pa],{v}@example.invalid/p"]
)
def test_r7_whitespace_breaks_opener_ownership(shape):
    text = shape.format(v=V)
    assert V not in redact_text(text)[0], (text, redact_text(text))



@pytest.mark.parametrize("prefix", ["(", "the users' (", "func(", "r'\\' ("])
def test_r7_unowned_call_pairing_never_hides_cookie_chain(prefix):
    text = prefix + "theme=dark; foo=" + V + ")"
    assert V not in redact_text(text)[0]



@pytest.mark.parametrize("template", ["Cookie: '{v}", 'cu' + 'rl ' + "-u 'alice:{v}", "--password '{v}"])
def test_r7_unclosed_value_quote_is_not_empty(template):
    assert V not in redact_text(template.format(v=V))[0]



