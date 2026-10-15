"""An exemption belongs only to its outer value, never nested assignments."""

import json

import pytest

from metamuse.safety import redact_text


@pytest.mark.parametrize(
    "shape",
    [
        'password=${X:-"api_key={v}"}',
        'token=os.getenv("T", "password={v}")',
        'password="<password={v}>"',
    ],
)
@pytest.mark.parametrize("layers", [0, 1, 2, 3])
def test_r46_exempt_outer_value_keeps_scanning_inside(shape, layers):
    marker = f"{'NESTED'}_MARKER_DEFAULT_VALUE"
    text = shape.replace("{v}", marker)
    for _ in range(layers):
        text = json.dumps({"code": text})
    result, findings = redact_text(text)
    assert marker not in result and findings, (text, result, findings)
    assert redact_text(result)[0] == result


def test_r46_nested_exempt_values_grow_linearly():
    from b16a_r3_complexity import cpu_samples
    from b16a_r4_complexity import complexity_criterion

    sizes = (16384, 32768, 65536, 131072)
    samples = []
    for size in sizes:
        count = size // len('token=${X:-}')
        text = 'token=${X:-' * count + '$VAR' + '}' * count
        samples.append(cpu_samples(text))
    assert complexity_criterion(samples)[0], samples
