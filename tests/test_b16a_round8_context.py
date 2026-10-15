"""Every old family and all new representations survive context selection."""

from collections import Counter

from b16a_r8_context import NATURAL, cases, rows, verify


def test_r8_context_keeps_all_native_rows_and_new_boundary_representations():
    pool = list(rows())
    assert len(pool) == 33850
    added = [r for r in pool if r.family.startswith("matrix:r8:")]
    assert len(added) == 395
    assert Counter(r.family.rsplit(":", 1)[-1] for r in added) == {
        "raw": 79,
        "json1": 79,
        "json_in_json": 79,
        "pct1": 79,
        "form_plus": 79,
    }
    selected = list(cases())
    families = {r.family for r in pool}
    for random in (False, True):
        assert {r.family for r, ctx, *_ in selected if (ctx == "random") == random} == families
    assert {ctx for _, ctx, *_ in selected} == {name for name, _ in NATURAL} | {"random"}
    result = verify()
    assert not result["failures"], result
    assert result["counts"]["leaks"] == result["counts"]["nonfixpoint"] == 0, result
