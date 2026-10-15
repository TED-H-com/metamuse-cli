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


def test_r5_assignment_closure_masks_unterminated_outer_value():
    from metamuse.safety import redact_text

    marker = f"{'CLOSURE'}_MARKER_PRIVATE_VALUE"
    text = 'sid=${"password":"' + marker + '"'
    output, findings = redact_text(text)
    assert output == "sid=[REDACTED]", (output, findings)
