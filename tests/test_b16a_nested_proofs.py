"""Nested structural proofs retain boundaries without restarting sparse edits."""

import json

import pytest

from metamuse import safety


class CountedEdits:
    def __init__(self, values):
        self.values = values
        self.iterations = self.visits = 0

    def __len__(self):
        return len(self.values)

    def __iter__(self):
        self.iterations += 1
        for value in self.values:
            self.visits += 1
            yield value


@pytest.mark.parametrize("depth", [24, 48])
def test_nested_escaped_objects_map_with_constant_edit_traversals(monkeypatch, depth):
    original = safety._decoded_proofs
    records = []

    def measured(ranges, edits):
        if not edits or not any(a < ranges[0][1] for i, (a, _) in enumerate(ranges) if i):
            return original(ranges, edits)
        counter = CountedEdits(edits)
        result = original(ranges, counter)
        records.append((len(ranges), len(edits), counter.iterations, counter.visits))
        return result

    monkeypatch.setattr(safety, "_decoded_proofs", measured)
    marker = "Nested_" + "Synthetic_7319"
    for value in (17, [marker]):
        text = (
            '{"label":"\\u0061","next":' * depth
            + json.dumps({"password": value, "public": "visible"})
            + "}" * depth
        )
        output, findings = safety.redact_text(text)
        parsed = json.loads(output)
        for _ in range(depth):
            assert parsed["label"] == "a"
            assert set(parsed) == {"label", "next"}
            parsed = parsed["next"]
        assert parsed == {"password": "[REDACTED]", "public": "visible"}
        assert marker not in output
        assert findings == [{"kind": "sensitive_field", "count": 1}]
        assert safety.redact_text(output) == (output, [])
    assert records
    print(json.dumps({"depth": depth, "nested_mapping_calls": records}))
    for proofs, edits, traversals, visits in records:
        assert traversals <= 2, (proofs, edits, traversals, visits)
        assert visits <= 2 * edits, (proofs, edits, traversals, visits)


@pytest.mark.parametrize("layout", ["nested_equal_starts", "crossing_and_collapsed_starts"])
def test_proof_mapping_preserves_duplicate_and_partial_escape_boundaries(layout):
    # One coalesced run maps three original percent escapes to three characters.
    # Boundaries inside an escape round outward to its exact contributing span.
    _, edits = safety._decode_view("L%41%42%43R%22S")
    assert len(edits) == 2
    pairs = (
        [(0, end) for end in range(1, 15)] + [(0, 14), (0, 14), (1, 10)]
        if layout == "nested_equal_starts"
        else [(0, 10), (2, 14), (3, 8), (4, 12), (4, 12), (5, 7), (6, 13)]
    )
    ranges = safety._QuoteRanges(sorted(pairs))

    def point(position, ending):
        shift = 0
        for edit in edits:
            if edit.original_end <= position:
                shift = edit.original_end - edit.end
            elif edit.original_start < position:
                width = edit.original_end - edit.original_start
                numerator = (position - edit.original_start) * (edit.end - edit.start)
                quotient, remainder = divmod(numerator, width)
                return edit.start + quotient + int(ending and remainder != 0)
            else:
                break
        return position - shift

    expected = sorted((point(a, False), point(b, True)) for a, b in ranges)
    counted = CountedEdits(edits)
    mapped = list(safety._decoded_proofs(ranges, counted))
    assert mapped == expected
    assert len(mapped) == len(pairs)
    assert counted.iterations <= 2
    assert counted.visits <= 2 * len(edits)
