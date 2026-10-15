"""Context-property regressions and coverage assertions for the seeded subset."""

from collections import Counter

from b16a_r6_context import LAYERS, NATURAL, cases, generate_rows, verify

from metamuse.safety import redact_text


def test_r6_context_subset_covers_every_context_and_representation():
    assert len(NATURAL) == 26
    selected = list(cases(3000))
    assert len(selected) == 3000
    assert {c[1] for c in selected} == {name for name, _ in NATURAL} | {"random"}
    assert {c[2] for c in selected} == set(LAYERS)
    assert {c[3] for c in selected} == {True, False}
    assert {c[0].family.split(":")[0] for c in selected} == {
        "review",
        "own",
        "matrix",
        "review_extension",
        "unclosed",
        "html",
        "benign",
    }
    result = verify(3000)
    assert result["counts"].get("leaks", 0) == 0, result
    assert result["counts"].get("nonfixpoint", 0) == 0, result
    assert result["counts"].get("benign_changed_N", 0) == 0, result


def test_r6_reviewer_neighbours_unchanged_family_coverage():
    rows = list(generate_rows())
    counts = Counter(row[0] for row in rows)
    assert len(rows) == 467
    # Pin every review family rather than just the aggregate row count.
    assert len(counts) == 113
    for family, text, markers, benign in rows:
        output, findings = redact_text(text)
        if benign:
            if family == "schemes_benign_csv" or (
                family == "schemes_benign_escaped_nl" and not text.startswith("ftp:")
            ):
                continue
            assert (output, findings) == (text, []), (family, text, output)
        else:
            assert not any(marker in output for marker in markers), (family, text, output)
            assert redact_text(output)[0] == output
