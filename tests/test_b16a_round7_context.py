"""Pin complete families and representations, rather than corpus prefixes."""

from collections import Counter
from itertools import islice

from b16a_r7_context import NATURAL, cases, rows, verify


def test_r7_context_subset_full_family_and_representation_coverage():
    pool = list(rows())
    matrix = [row for row in pool if row.family.startswith("matrix:")]
    assert len(matrix) == 31362
    families = {row.family for row in pool}
    assert len(families) >= 400
    selected = list(cases())
    for kind in ("N", "R"):
        assert {r.family for r, c, *_ in selected if (c == "random") == (kind == "R")} == families
    assert {c for _, c, *_ in selected} == {n for n, _ in NATURAL} | {"random"}
    reps = Counter(r.family.rsplit(":", 1)[-1] for r in matrix)
    assert set(reps) == {
        "raw",
        "pct1",
        "pct2",
        "pct3",
        "pct1_lower",
        "form_plus",
        "jsonesc",
        "json_in_json",
    }
    result = verify()
    assert not result["failures"], result
    assert result["counts"].get("leaks", 0) == result["counts"].get("nonfixpoint", 0) == 0, result


def test_r7_full_context_product_has_both_placements_per_context():
    selected = list(islice(cases(full=True), (len(NATURAL) + 8) * 2))
    assert len({r for r, *_ in selected}) == 1
    for name, _ in NATURAL:
        assert {inside for _, c, inside, _ in selected if c == name} == {False, True}
    assert sum(c == "random" for _, c, *_ in selected) == 16
    assert len(selected) == 102
