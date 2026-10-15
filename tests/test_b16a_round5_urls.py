"""Authority candidates must neither skip neighbours nor consume unrelated fields."""

import json
from urllib.parse import quote

import pytest

from metamuse.safety import redact_text


def encode(text, layer):
    if layer == "percent":
        return quote(text, safe="")
    for _ in range(int(layer)):
        text = json.dumps({"note": text})
    return text


FIRST = (
    "https://example.invalid",
    "https://example.invalid:8443",
    "https://example.invalid/a",
    "https://example.invalid/?q=ok",
    "https://alice:other@example.invalid",
)
JOINS = (",", ";", ", ", "|", '","private":"', '","', "' '", "](")


@pytest.mark.parametrize("layer", ["0", "1", "2", "percent"])
@pytest.mark.parametrize("first", FIRST)
@pytest.mark.parametrize("join", JOINS)
def test_r41_each_url_owns_its_authority(first, join, layer):
    marker = f"{'URL'}_MARKER_SECOND_AUTHORITY"
    text = encode(first + join + f"https://alice:{marker}@example.invalid/path", layer)
    result, findings = redact_text(text)
    assert marker not in result, (text, result, findings)
    assert findings
    expected = encode(
        first.replace(":other@", ":[REDACTED]@")
        + join
        + "https://alice:[REDACTED]@example.invalid/path",
        layer,
    ).replace("%5BREDACTED%5D", "[REDACTED]")
    assert result == expected, (text, result, expected)
    assert redact_text(result)[0] == result


@pytest.mark.parametrize("layer", ["0", "1", "2", "percent"])
@pytest.mark.parametrize("first", FIRST[:4])
@pytest.mark.parametrize("join", JOINS)
@pytest.mark.parametrize("second", ["alice@example.invalid", "@handle"])
def test_r41_benign_neighbours(first, join, layer, second):
    text = encode(json.dumps([first, join, second]), layer)
    assert redact_text(text) == (text, [])


def test_r41_compact_json_and_userinfo_punctuation():
    text = '{"url":"https://example.invalid","email":"alice@example.invalid"}'
    assert redact_text(text) == (text, [])
    for punctuation in (",", ";", ")", "]", "'", "@"):
        marker = f"{'URL'}{punctuation}MARKER_AUTHORITY"
        text = f"https://alice:{marker}@example.invalid/a"
        result, findings = redact_text(text)
        assert marker not in result and findings


@pytest.mark.parametrize("layers", [1, 2, 3])
def test_r41_mixed_layer_next_scheme_is_authority_boundary(layers):
    marker = f"{'URL'}_MARKER_MIXED_AUTHORITY"
    second = f"https://alice:{marker}@example.invalid/a"
    for _ in range(layers):
        second = quote(second, safe="")
    text = "https://example.invalid|" + second
    output, findings = redact_text(text)
    expected = "https://example.invalid|" + second.replace(marker, "[REDACTED]")
    assert output == expected, (output, expected, findings)
