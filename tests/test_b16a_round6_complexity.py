"""Growth is measured over the complete, internally iterated redaction call."""

import pytest
from b16a_r3_complexity import cpu_samples
from b16a_r4_complexity import GROWTH_SIZES
from b16a_r6_complexity import build, complexity_criterion, families
from b16a_r6_memory import measure

from metamuse.safety import redact_text


@pytest.mark.parametrize("family", [f for f in families() if f.startswith("r6_")])
def test_r6_linear_exemption_and_recovery_growth(family):
    samples = []
    for size in GROWTH_SIZES:
        text = build(family, size)
        redact_text(text)
        samples.append(cpu_samples(text))
    accepted, slope, overall = complexity_criterion(samples)
    ratios = [b / a for a, b in zip(samples, samples[1:], strict=False)]
    assert accepted, (family, samples, ratios, slope, overall)


@pytest.mark.parametrize(
    "family",
    [
        "review_xml_attr_quote_run",
        "escaped_quote_run",
        "r6_har_swallowed_candidates",
        "r6_unclosed_getenv",
    ],
)
def test_r6_fresh_process_memory_budget(family):
    row = measure(family, 2 * 1024 * 1024)
    assert row["rss_kib"] <= 1024 * 1024 // 10 + 48 * 1024, row
