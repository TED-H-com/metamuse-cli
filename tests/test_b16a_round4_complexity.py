"""CPU ratios on the actual iterated public call, never wall-clock alarms."""

import pytest
from b16a_r4_complexity import families, growth


@pytest.mark.parametrize("family", families())
def test_r3_iterated_linear_cpu_growth(family):
    samples, ratios = growth(family)
    assert max(ratios) <= 2.5, (family, samples, ratios)
