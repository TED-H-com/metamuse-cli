"""The full seeded generators also run explicitly for the report."""

from itertools import islice

from b16a_r4_corpus import extended_corpus, realistic_notes, verify


def test_r33_extended_fixpoint_corpus_subset():
    assert verify(extended_corpus(2000))["inputs"] == 2000


def test_r33_realistic_forms_fixpoint_and_no_credential_visible():
    result = verify(islice(realistic_notes(), 1000), tagged=True)
    assert result["inputs"] == 1000 and result["visible"] == result["unstable"] == 0
