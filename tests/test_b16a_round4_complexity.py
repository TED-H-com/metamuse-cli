"""Robust CPU growth on the iterated public call, never wall-clock alarms."""

import pytest
from b16a_r4_complexity import complexity_criterion, families, growth


@pytest.mark.parametrize("family", families())
def test_r3_iterated_linear_cpu_growth(family):
    samples, ratios = growth(family, warmup=True)
    passed, slope, overall = complexity_criterion(samples)
    assert passed, (family, samples, ratios, {"log_log_slope": slope, "end_ratio": overall})


@pytest.mark.parametrize(
    "samples, expected",
    [
        pytest.param([0.012674, 0.034242, 0.049960, 0.099881], True, id="ci-encoded-key"),
        pytest.param([0.008444, 0.021286, 0.041757, 0.081970], True, id="ci-sk-body"),
        pytest.param([1, 2.7, 5.4, 10.8], True, id="linear-first-2.7-step"),
        pytest.param([1, 2, 5.4, 10.8], True, id="linear-middle-2.7-step"),
        pytest.param([1, 2, 4, 10.8], True, id="linear-last-2.7-step"),
        pytest.param([1, 4, 16, 64], False, id="quadratic"),
        pytest.param([1, 3, 9, 27], False, id="threefold"),
        pytest.param([2 ** (1.5 * i) for i in range(4)], False, id="power-1.5"),
        pytest.param([1, 0.3, 4, 8], False, id="slope-alone-fails"),
        pytest.param([1, 4, 8, 12.1], False, id="end-ratio-alone-fails"),
    ],
)
def test_r4b_complexity_criterion_sensitivity(samples, expected):
    assert complexity_criterion(samples)[0] is expected
