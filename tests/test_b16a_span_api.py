"""Additive private spans compose with complete shared credential extents."""

import json
from urllib.parse import quote_plus

import pytest

from metamuse import safety


def test_private_spans_union_with_known_url_and_complete_header():
    marker = "Span_" + "Synthetic_2417"
    known = f"wss://{marker}.fixture.test/private?auth_token={marker}"
    text = f"note: {known}\nCookie: a={marker};\r\n b={marker}\nX-Public: visible"
    calls = []

    def private(view, *, original):
        calls.append((view, original))
        start = view.index(known)
        return [(start, start + len(known))]

    output, findings = safety.redact_text(text, private_spans=private)
    assert output == "note: [REDACTED]\nCookie: [REDACTED]\nX-Public: visible"
    assert findings == [
        {"kind": "cookie", "count": 1},
        {"kind": "sensitive_field", "count": 1},
    ]
    assert calls == [(text, True)]
    assert safety.redact_text(output) == (output, [])


@pytest.mark.parametrize("representation", ["pct3", "json3", "form", "json_escape"])
def test_private_spans_decoded_mapping_preserves_public_boundaries(representation):
    marker = "Span_" + "Synthetic_2417"
    decoded = f"left {marker} right"
    if representation == "pct3":
        text = decoded
        for _ in range(3):
            text = "".join(f"%{ord(char):02X}" for char in text)
    elif representation == "json3":
        text = json.dumps({"note": decoded})
        for _ in range(2):
            text = json.dumps(text)
    elif representation == "form":
        text = "q=" + quote_plus(decoded)
    else:
        text = json.dumps({"note": decoded}).replace("S", r"\u0053")
    calls = []

    def private(view, *, original):
        calls.append((view, original))
        if original:
            return []
        start = view.index(marker)
        return [(start, start + len(marker))]

    output, findings = safety.redact_text(text, private_spans=private)
    assert findings == [{"kind": "sensitive_field", "count": 1}]
    assert calls[0] == (text, True)
    assert len(calls) == 2 and calls[1][1] is False
    assert marker in calls[1][0]
    if representation in {"json3", "json_escape"}:
        value = json.loads(output)
        if representation == "json3":
            value = json.loads(json.loads(value))
        assert value == {"note": "left [REDACTED] right"}
    elif representation == "form":
        assert output == "q=left+[REDACTED]+right"
    else:
        from urllib.parse import unquote

        value = output
        for _ in range(3):
            value = unquote(value)
        assert value == "left [REDACTED] right"
        assert "%" not in output[output.index("[REDACTED]") : output.index("[REDACTED]") + 10]
    assert safety.redact_text(output) == (output, [])


@pytest.mark.parametrize("category", ["exact_int", "bounds", "ordering", "shape", "iterable"])
def test_private_spans_invalid_output_never_returns_partial_success(category):
    invalid = {
        "exact_int": [[(True, 3)], [(0, False)], [(0, 1.2)]],
        "bounds": [[(-1, 1)], [(0, 0)], [(2, 1)], [(0, 99)]],
        "ordering": [[(3, 4), (0, 1)], [(0, 3), (2, 4)]],
        "shape": [[(0, 1, 2)], [(0,)], ["ab"]],
        "iterable": [None, True, 42],
    }
    for spans in invalid[category]:
        with pytest.raises(ValueError, match="^Invalid additive private spans$") as caught:
            safety.redact_text(
                "public note", private_spans=lambda view, *, original, spans=spans: spans
            )
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None


def test_private_spans_callback_failure_after_valid_span_is_fixed():
    marker = "Callback_" + "Synthetic_2417"

    for fail_original in (True, False):

        def private(view, *, original, fail_original=fail_original):
            if original == fail_original:
                yield 0, 1
                raise RuntimeError(marker)

        with pytest.raises(ValueError, match="^Invalid additive private spans$") as caught:
            safety.redact_text("note%3A" + marker, private_spans=private)
        assert marker not in str(caught.value)
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None


def test_private_spans_first_application_only_and_core_cannot_be_disabled():
    marker = "Core_" + "Synthetic_2417"
    text = f"password={marker}"
    calls = []

    def private(view, *, original):
        calls.append((view, original))
        return []

    assert safety.redact_text(text, private_spans=private) == safety.redact_text(text)
    assert calls == [(text, True)]
    # Adjacent valid additions coalesce through the existing union rules.
    text = f"left {marker} right"
    start = text.index(marker)
    output, findings = safety.redact_text(
        text,
        private_spans=lambda view, *, original: [
            (start, start + 2),
            (start + 2, start + len(marker)),
        ],
    )
    assert (output, findings) == (
        "left [REDACTED] right",
        [{"kind": "sensitive_field", "count": 1}],
    )


@pytest.mark.parametrize("changes", [3, 4])
def test_private_spans_keep_exact_four_application_bound(monkeypatch, changes):
    calls = []

    def hook(view, *, original):
        return []

    def once(text, **kwargs):
        calls.append(kwargs)
        return (text + "x" if len(calls) <= changes else text), []

    monkeypatch.setattr(safety, "_redact_once", once)
    output, findings = safety.redact_text("public", private_spans=hook)
    assert calls == [{"private_spans": hook}, {}, {}, {}]
    if changes == 3:
        assert (output, findings) == ("publicxxx", [])
    else:
        assert (output, findings) == (
            "[REDACTED]",
            [{"kind": "redaction_unstable", "count": 1}],
        )


def test_private_spans_empty_hook_does_not_rebuild_span_indexes(monkeypatch):
    counts = {}
    for name in ("_SpanIndex", "_merge_spans"):
        original = getattr(safety, name)

        def measured(*args, _name=name, _original=original, **kwargs):
            counts[_name] = counts.get(_name, 0) + 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(safety, name, measured)
    marker = "Index_" + "Synthetic_2417"
    for text in ("public prose", f"password={marker}", f"?token%253D{marker}"):
        counts.clear()
        expected = safety.redact_text(text)
        ordinary_counts = counts.copy()
        counts.clear()
        assert safety.redact_text(text, private_spans=None) == expected
        assert counts == ordinary_counts
        counts.clear()
        assert safety.redact_text(text, private_spans=lambda view, *, original: []) == expected
        assert counts == ordinary_counts
