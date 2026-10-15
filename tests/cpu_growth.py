"""Sample CPU growth without comparing noisy neighbouring measurements."""

import json
import math
import statistics
import time
from dataclasses import dataclass

SAMPLES = 3


@dataclass
class CpuRow:
    size: int
    samples: list[float]

    @property
    def cpu(self):
        return min(self.samples)


def sample_cpu(call, *, repeats=1):
    """Time a fixed batch; callers prepare inputs and fresh write state first."""
    start = time.process_time()
    for _ in range(repeats):
        call()
    return time.process_time() - start


def assert_cpu_growth(rows, *, label, ceiling=None):
    assert len(rows) >= 4
    assert all(b.size == 2 * a.size for a, b in zip(rows, rows[1:], strict=False))
    assert all(len(row.samples) >= SAMPLES for row in rows)
    xs = [math.log(row.size) for row in rows]
    ys = [math.log(row.cpu) for row in rows]
    xmean, ymean = statistics.fmean(xs), statistics.fmean(ys)
    slope = sum((x - xmean) * (y - ymean) for x, y in zip(xs, ys, strict=True)) / sum(
        (x - xmean) ** 2 for x in xs
    )
    ratio = rows[-1].cpu / rows[0].cpu
    size_ratio = rows[-1].size / rows[0].size
    evidence = json.dumps(
        {
            "label": label,
            "rows": [{"size": row.size, "samples": row.samples, "cpu": row.cpu} for row in rows],
            "slope": slope,
            "ratio": ratio,
            "ratio_limit": 1.5 * size_ratio,
        },
        sort_keys=True,
    )
    print("CPU_GROWTH " + evidence, flush=True)
    assert slope <= 1.25, evidence
    assert ratio <= 1.5 * size_ratio, evidence
    if ceiling is not None:
        assert all(cpu < ceiling for row in rows for cpu in row.samples), evidence
