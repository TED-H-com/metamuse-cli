"""Default deterministic class subset; the full matrix runs separately for evidence."""

from b16a_r5_corpus import seeded_subset, verify


def test_r5_class_matrix_seeded_subset():
    result = verify(seeded_subset())
    assert (
        result.get("leaks", 0)
        == result.get("nonfixpoint", 0)
        == result.get("benign_changed", 0)
        == 0
    ), result
