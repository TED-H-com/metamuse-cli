"""CPU growth ratios, never wall-clock alarms, on every review/new-rule family."""

import pytest
from b16a_r3_complexity import UNITS, growth


@pytest.mark.parametrize("family", tuple(UNITS))
def test_r2_2_linear_cpu_growth(family):
    samples, ratios = growth(family)
    assert max(ratios) <= 2.5, (family, samples, ratios)
