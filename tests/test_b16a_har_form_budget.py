"""Form-owned HAR decoding keeps independent budgets and source boundaries."""

import json
from urllib.parse import parse_qs, quote, quote_plus

import pytest

from metamuse import safety


def render(text, spans):
    parts = []
    position = 0
    for start, end, _, replacement in spans:
        assert position <= start < end <= len(text)
        parts.extend((text[position:start], replacement))
        position = end
    return "".join([*parts, text[position:]])


@pytest.mark.parametrize("encoding", [quote, quote_plus], ids=["percent_spaces", "plus_spaces"])
@pytest.mark.parametrize("shape", ["cookies", "string", "number", "container"])
def test_form_owned_har_masks_at_exhausted_other_budgets(encoding, shape):
    marker = "FormBudget_" + "Synthetic_7319"
    values = {"string": marker, "number": 7319, "container": [marker]}
    record = {"name": "password", "value": values.get(shape, marker), "public": "inner"}
    plain = json.dumps(
        {"cookies" if shape == "cookies" else "entries": [record], "public": "visible"}
    )
    text = "q=" + encoding(plain, safe="") + "&outside=public"
    spans = list(safety._har_spans(text, _depth=3, _percent_depth=3, _form_depth=2))
    assert len(spans) == 1
    output = render(text, spans)
    parsed = parse_qs(output)
    expected = (
        {"cookies": "[REDACTED]", "public": "visible"}
        if shape == "cookies"
        else {
            "entries": [{"name": "password", "value": "[REDACTED]", "public": "inner"}],
            "public": "visible",
        }
    )
    assert json.loads(parsed["q"][0]) == expected
    assert parsed["outside"] == ["public"]
    assert marker not in output
    assert list(safety._har_spans(output, _depth=3, _percent_depth=3, _form_depth=2)) == []
    assert safety.redact_text(output) == (output, [])


@pytest.mark.parametrize("layers", [1, 2, 3])
def test_nested_form_fields_use_only_their_remaining_form_steps(layers):
    marker = "FormBudget_" + "Synthetic_7319"
    plain = json.dumps({"cookies": [{"name": "theme", "value": marker}], "public": "visible"})
    text = plain
    for _ in range(layers):
        text = "q=" + quote_plus(text)
    output = render(
        text,
        safety._har_spans(text, _depth=3, _percent_depth=3, _form_depth=3 - layers),
    )
    decoded = output
    for _ in range(layers):
        decoded = parse_qs(decoded)["q"][0]
    assert json.loads(decoded) == {"cookies": "[REDACTED]", "public": "visible"}
    assert marker not in output
    assert safety.redact_text(output) == (output, [])
    assert list(safety._har_candidates(text, _depth=3, _percent_depth=3, _form_depth=3)) == []


def test_form_percent_decoding_does_not_reinterpret_string_data_as_har_structure():
    # After the form step this is one benign JSON string value, not new keys.
    literal = "%22cookies%22:%5B%7B%22name%22:%22sid%22,%22value%22:%22public%22%7D%5D"
    plain = json.dumps({"name": "theme", "value": literal, "public": "visible + %22"})
    text = "q=" + quote_plus(plain)
    assert list(safety._har_candidates(text, _depth=3, _percent_depth=3)) == []


def test_sensitive_outer_headers_keep_their_whole_mask_after_har_mapping():
    marker = "FormBudget_" + "Synthetic_7319"
    for value in (marker, 7319, [marker]):
        for encoding in (quote, quote_plus):
            plain = json.dumps(
                {
                    "headers": [{"name": "password", "value": value, "public": "inner"}],
                    "public": "visible",
                }
            )
            text = "q=" + encoding(plain, safe="") + "&outside=public"
            intermediate = render(
                text,
                safety._har_spans(text, _depth=3, _percent_depth=3, _form_depth=2),
            )
            # The HAR helper masks one value. The public API additionally owns
            # the sensitive outer headers field; its contents are not public.
            assert json.loads(parse_qs(intermediate)["q"][0]) == {
                "headers": [{"name": "password", "value": "[REDACTED]", "public": "inner"}],
                "public": "visible",
            }
            output, findings = safety.redact_text(intermediate)
            assert json.loads(parse_qs(output)["q"][0]) == {
                "headers": "[REDACTED]",
                "public": "visible",
            }
            assert parse_qs(output)["outside"] == ["public"]
            assert marker not in output
            assert len(findings) == 1 and findings[0]["count"] == 1
            assert safety.redact_text(output) == (output, [])
