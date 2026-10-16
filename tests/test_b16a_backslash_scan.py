"""Backslash runs retain literal output and bounded decoded-header scanning."""

import json

import pytest
from b16a_r3_complexity import cpu_samples
from b16a_r4_complexity import build, complexity_criterion

from metamuse import safety


def test_backslash_run_small_growth_and_literal_output():
    sizes = (8192, 32768)
    samples = []
    for size in sizes:
        text = build("review_backslash_run", size)
        assert text == chr(92) * size
        assert safety.redact_text(text) == (text, [])
        # The same min-of-three CPU sampler and criterion as the original case.
        samples.append(cpu_samples(text))
    passed, slope, overall = complexity_criterion(samples, sizes)
    print(json.dumps({"sizes": sizes, "cpu": samples, "slope": slope, "ratio": overall}))
    assert passed, (samples, {"log_log_slope": slope, "end_ratio": overall})


@pytest.mark.parametrize("escape", ["backslash", "quoted", "unicode_backslash"])
def test_adjacent_escape_edits_preserve_exact_coalesced_boundaries(escape):
    unit, decoded = {
        "backslash": (chr(92) * 2, chr(92)),
        "quoted": (chr(92) + '"', '"'),
        "unicode_backslash": (chr(92) + "u005c", chr(92)),
    }[escape]
    count = 24
    source = "left " + unit * count + " right"
    view, edits = safety._decode_view(source, json_escapes=True)
    assert view == "left " + decoded * count + " right"
    assert len(edits) == 1
    edit = edits[0]
    assert (edit.start, edit.end, edit.original_start, edit.original_end) == (
        5,
        5 + count,
        5,
        5 + len(unit) * count,
    )
    for offset in (0, 1, count - 1):
        start, end, kind, replacement = next(
            safety._original_spans([(5 + offset, 6 + offset, "sensitive_field", "redacted")], edits)
        )
        assert (start, end) == (5 + len(unit) * offset, 5 + len(unit) * (offset + 1))
        assert source[:start] + replacement + source[end:] == (
            "left " + unit * offset + "redacted" + unit * (count - offset - 1) + " right"
        )
        assert kind == "sensitive_field"


def test_sparse_escaped_newlines_keep_header_folds_and_public_boundaries():
    marker = "Backslash_" + "Synthetic_7319"
    for newline in ("\n", "\r\n"):
        plain = f"Cookie: a={marker};{newline} b={marker}{newline}X-Public: visible"
        for text in (plain, json.dumps({"note": plain, "public": "visible"})):
            output, findings = safety.redact_text(text)
            assert marker not in output
            assert "X-Public: visible" in output
            assert findings == [{"kind": "cookie", "count": 1}]
            if text != plain:
                assert json.loads(output) == {
                    "note": "Cookie: [REDACTED]" + newline + "X-Public: visible",
                    "public": "visible",
                }
            assert safety.redact_text(output) == (output, [])


def test_disjoint_escapes_leave_public_data_and_private_marker_mapping_intact():
    marker = "Backslash_" + "Synthetic_7319"
    plain = f"left {chr(92)} right {marker} public"
    text = json.dumps({"note": plain, "public": "visible"})
    calls = []

    def private(view, *, original):
        calls.append(original)
        if original:
            return []
        start = view.index(marker)
        return [(start, start + len(marker))]

    output, findings = safety.redact_text(
        text, private_spans=private, private_placeholder="redacted"
    )
    assert json.loads(output) == {
        "note": f"left {chr(92)} right redacted public",
        "public": "visible",
    }
    assert calls == [True, False]
    assert findings == [{"kind": "sensitive_field", "count": 1}]
    assert safety.redact_text(output) == (output, [])
