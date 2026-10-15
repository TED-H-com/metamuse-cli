"""Round-4 privacy regressions; all credential values are synthesized at runtime."""

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
    with pytest.raises(CoreError) as refused:
        _check_question(text)
    assert refused.value.detail.code == ErrorCode.INPUT_INVALID
    assert value not in str(refused.value)
    return output


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
