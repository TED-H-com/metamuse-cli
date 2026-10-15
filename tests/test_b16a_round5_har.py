"""HAR fields are interpreted at their JSON layer, with a strict partial lexer."""

import json
from urllib.parse import quote

import pytest

from metamuse.safety import redact_text


@pytest.mark.parametrize(
    "earlier", ["https://example.invalid/?q=%22x", '5" screen', "%7B%22cookies%22"]
)
@pytest.mark.parametrize("value_first", [False, True])
@pytest.mark.parametrize("layer", [0, 1, 2, 3, "percent"])
def test_r43_har_json_values_never_become_structure(earlier, value_first, layer):
    marker = f"{'HAR'}_MARKER_PRIVATE_VALUE"
    entry = (
        {"value": 'ab"' + marker, "name": "sid"}
        if value_first
        else {"name": "sid", "value": marker}
    )
    text = json.dumps(
        {
            "url": earlier,
            "headers": [{"name": "q", "value": earlier}, entry],
            "cookies": [{"name": "theme", "value": marker}],
        }
    )
    if layer == "percent":
        text = quote(text, safe="")
    else:
        for _ in range(layer):
            text = json.dumps({"capture": text})
    result, findings = redact_text(text)
    assert marker not in result, (text, result, findings)
    assert findings and redact_text(result)[0] == result


@pytest.mark.parametrize("value", [int(f"{98765432}{10123456}"), True, False, None])
@pytest.mark.parametrize("reverse", [False, True])
def test_r43_har_scalar_values_are_redacted(value, reverse):
    entry = {"value": value, "name": "session"} if reverse else {"name": "session", "value": value}
    result, findings = redact_text(json.dumps(entry))
    assert json.loads(result)["value"] == "[REDACTED]", (result, findings)
    assert findings


def test_r43_truncated_har_stops_at_first_error():
    marker = f"{'HAR'}_MARKER_TRUNCATED_VALUE"
    text = json.dumps({"headers": [{"name": "Authorization", "value": marker}]})
    result, findings = redact_text(text[:-4])
    assert marker[:10] not in result and findings
    # Corrupt syntax is never skipped to guess that a later object is HAR.
    broken = 'broken [{"name":"sid","value":"' + marker + '"}]'
    assert redact_text(broken) == (broken, [])


@pytest.mark.parametrize(
    "text",
    [
        '{"url":"https://example.invalid/?q=%22x","value":"5\\" screen"}',
        '{"name":"theme","value":true}',
        '{"note":"%7B%22name%22:%22sid%22,%22value%22:42%7D"}',
    ],
)
def test_r43_benign_json_values_are_data(text):
    assert redact_text(text) == (text, [])


def test_r43_form_wrapped_har_and_no_resynchronization():
    from urllib.parse import quote_plus

    marker = f"{'HAR'}_MARKER_FORM_PRIVATE"
    text = "q=" + quote_plus(json.dumps({"cookies": [{"name": "theme", "value": marker}]}))
    result, findings = redact_text(text)
    assert marker not in result and findings
    broken = '{"broken":oops,"next":{"name":"sid","value":"' + marker + '"}}'
    assert redact_text(broken) == (broken, [])
