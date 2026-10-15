"""New URL, quote, lexer and nesting families share the robust CPU criterion."""

import pytest
from b16a_r3_complexity import cpu_samples
from b16a_r4_complexity import GROWTH_SIZES, complexity_criterion
from b16a_r5_complexity import build, families, prior_families


@pytest.mark.parametrize("family", [f for f in families() if f not in prior_families()])
def test_r5_linear_cpu_growth(family):
    from metamuse.safety import redact_text

    samples = []
    for size in GROWTH_SIZES:
        text = build(family, size)
        redact_text(text)
        samples.append(cpu_samples(text))
    accepted, slope, overall = complexity_criterion(samples)
    ratios = [b / a for a, b in zip(samples, samples[1:], strict=False)]
    assert accepted, (family, samples, ratios, slope, overall)
