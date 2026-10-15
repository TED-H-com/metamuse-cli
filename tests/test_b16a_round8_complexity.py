"""Keep the new separator/layer families under the existing CPU/RSS contract."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from b16a_r3_complexity import cpu_samples
from b16a_r4_complexity import GROWTH_SIZES
from b16a_r8_complexity import NEW, build, complexity_criterion

from metamuse.safety import redact_text


@pytest.mark.parametrize("family", (*NEW, "r8_escape_run", "r8_quote_run"))
def test_r8_scope_cpu_growth(family):
    samples = []
    for size in GROWTH_SIZES:
        text = build(family, size)
        redact_text(text)
        samples.append(cpu_samples(text))
    accepted, slope, overall = complexity_criterion(samples)
    ratios = [b / a for a, b in zip(samples, samples[1:], strict=False)]
    assert accepted, (family, samples, ratios, slope, overall)


def test_r8_json_scope_fresh_child_rss():
    helper = Path(__file__).with_name("b16a_r8_measure.py")
    env = dict(os.environ, UV_OFFLINE="1", PYTHONPATH=os.pathsep.join(sys.path))
    result = subprocess.run(
        [
            sys.executable,
            str(helper),
            "driver",
            "--family",
            "r8_json_escape_parity",
            "--size",
            str(2 * 1024 * 1024),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    )
    row = json.loads(result.stdout)
    # One tenth of the 20 MiB budget, plus the measured interpreter baseline
    # and a fixed 32 MiB allocator headroom. No fixture lives on tmpfs.
    assert row["rss_kib"] <= row["baseline_kib"] + 1024 * 1024 // 10 + 32 * 1024, row
