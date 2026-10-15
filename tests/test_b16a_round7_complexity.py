"""Iterated calls must retain a linear CPU slope for each inner-loop family."""

import pytest
from b16a_r3_complexity import cpu_samples
from b16a_r4_complexity import GROWTH_SIZES
from b16a_r7_complexity import NEW, build, complexity_criterion

from metamuse.safety import redact_text


@pytest.mark.parametrize("family", ["r7_" + name for name in NEW])
def test_r7_every_scanner_linear(family):
    samples = []
    for size in GROWTH_SIZES:
        text = build(family, size)
        redact_text(text)
        samples.append(cpu_samples(text))
    accepted, slope, ratio = complexity_criterion(samples)
    assert accepted, (family, samples, slope, ratio)


@pytest.mark.parametrize(
    "family",
    [
        "r7_xml_unclosed_before_exempt",
        "r7_html_quoted_subtags",
        "r7_folded_header",
        "r7_doubled_quotes",
        "cookie_header_multiline",
    ],
)
def test_r7_fresh_process_memory(family):
    from b16a_r7_memory import measure

    row = measure(family, 2 * 1024 * 1024)
    assert row["rss_kib"] <= 1024 * 1024 // 10 + 48 * 1024, row
