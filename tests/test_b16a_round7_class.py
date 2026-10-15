"""A seeded public-output product complements the exact D2 cut assertions."""

from b16a_r7_class import FAMILIES, rows, verify


def test_r7_closer_follower_product_seeded_subset():
    selected = list(rows())
    assert len(selected) == 1000
    assert {r[0] for r in selected} == set(FAMILIES)
    assert {r[1] for r in selected} == set("'\"`”’)]}>")
    result = verify()
    assert not result["failures"], result
    assert result["counts"].get("nonfixpoint", 0) == 0, result
