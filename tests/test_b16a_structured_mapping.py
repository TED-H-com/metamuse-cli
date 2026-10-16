"""Synthetic structured masks preserve their actual encoding and public siblings."""

import json
from urllib.parse import quote, quote_plus, unquote, unquote_plus

import pytest

from metamuse import safety


def representations(plain):
    yield "raw", plain, lambda output: output
    for depth in range(1, 4):
        text = plain
        for _ in range(depth):
            text = quote(text, safe="")

        def decode(output, depth=depth):
            for _ in range(depth):
                output = unquote(output)
            return output

        yield f"percent{depth}", text, decode
        text = json.dumps({"note": plain})
        for _ in range(depth - 1):
            text = json.dumps(text)

        def decode(output, depth=depth):
            for _ in range(depth):
                output = json.loads(output)
            assert set(output) == {"note"}
            return output["note"]

        yield f"json{depth}", text, decode
    yield "form", "q=" + quote_plus(plain), lambda output: unquote_plus(output.removeprefix("q="))

    yield (
        "json_percent",
        json.dumps({"note": quote(plain, safe="")}),
        lambda output: unquote(json.loads(output)["note"]),
    )
    yield (
        "percent_json",
        quote(json.dumps({"note": plain}), safe=""),
        lambda output: json.loads(unquote(output))["note"],
    )


@pytest.mark.parametrize("representation", ["raw", "percent1", "json2"])
def test_structured_root_baselines_preserve_json_and_public_sibling(representation):
    marker = "Structured_" + "Synthetic_7319"
    plain = json.dumps({"password": [marker], "public": "visible"})
    for name, text, decode in representations(plain):
        if name != representation:
            continue
        output, findings = safety.redact_text(text)
        assert marker not in output
        assert "visible" in output
        assert json.loads(decode(output)) == {"password": "[REDACTED]", "public": "visible"}
        assert findings == [{"kind": "sensitive_field", "count": 1}]
        assert safety.redact_text(output) == (output, [])


def test_structured_scalars_and_containers_at_every_supported_layer():
    marker = "Structured_" + "Synthetic_7319"
    values = [marker, [marker], {"inner": marker}, [], {}, 7319, None, True, False]
    for value in values:
        # Empty braces are the existing closed format-field placeholder;
        # null/booleans likewise retain the established literal exemptions.
        expected = value if value is None or type(value) is bool or value == {} else "[REDACTED]"
        plain = json.dumps({"password": value, "public": "visible"})
        for name, text, decode in representations(plain):
            for hook in (None, lambda view, *, original: []):
                output, findings = safety.redact_text(text, private_spans=hook)
                assert marker not in output, (value, name, output)
                assert json.loads(decode(output)) == {
                    "password": expected,
                    "public": "visible",
                }, (value, name, output)
                assert safety.redact_text(output) == (output, []), (value, name, findings)
                assert findings == (
                    [] if expected == value else [{"kind": "sensitive_field", "count": 1}]
                )


def test_structured_known_prefix_additions_cannot_displace_core_quotes():
    marker = "gh" + "p_" + ("Ab3" * 12)
    plain = json.dumps({"password": [marker], "public": "visible"})
    for name, text, decode in representations(plain):
        for extent in ("inner", "whole", "same_start_shorter"):
            for placeholder in ("[REDACTED]", "redacted"):

                def private(view, *, original, extent=extent):
                    if extent == "inner":
                        start = view.find(marker)
                        return [(start, start + len(marker))] if start >= 0 else []
                    start = view.find('["')
                    if start < 0:
                        return []
                    end = view.index("]", start) + 1 if extent == "whole" else start + 2
                    return [(start, end)]

                output, findings = safety.redact_text(
                    text, private_spans=private, private_placeholder=placeholder
                )
                assert marker not in output, (name, extent)
                assert json.loads(decode(output)) == {
                    "password": "[REDACTED]",
                    "public": "visible",
                }, (name, extent, output)
                assert findings and safety.redact_text(output) == (output, [])


def test_har_structured_scalar_masks_keep_source_quote_layer_and_siblings():
    marker = "Structured_" + "Synthetic_7319"
    for value in ([marker], {"inner": marker}, 7319, None, True, False):
        for reverse in (False, True):
            record = {"name": "password", "value": value, "public": "visible"}
            if reverse:
                record = dict(reversed(record.items()))
            plain = json.dumps({"entries": [record], "public": "visible"})
            for name, text, decode in representations(plain):
                output, findings = safety.redact_text(text)
                parsed = json.loads(decode(output))
                assert parsed == {
                    "entries": [{"name": "password", "value": "[REDACTED]", "public": "visible"}],
                    "public": "visible",
                }, (value, reverse, name, output)
                assert marker not in output
                assert findings and safety.redact_text(output) == (output, [])


def test_unrelated_escapes_do_not_change_a_raw_structured_masks_layer():
    marker = "Structured_" + "Synthetic_7319"
    for value in ([marker], [], {}, 7319):
        for note in ("line\nbreak", json.dumps(json.dumps({"public": "visible"})), "%22public%22"):
            plain = json.dumps({"password": value, "note": note, "public": "visible"})
            for name, text, decode in representations(plain):
                output, findings = safety.redact_text(text)
                assert json.loads(decode(output)) == {
                    "password": {} if value == {} else "[REDACTED]",
                    "note": note,
                    "public": "visible",
                }, (value, note, name, output)
                assert bool(findings) is (value != {})
                assert safety.redact_text(output) == (output, [])


def test_independent_structured_masks_keep_distinct_quote_anchors():
    marker = "Structured_" + "Synthetic_7319"
    inner = json.dumps({"password": [marker], "public": "inner"})
    for reverse in (False, True):
        record = {"password": [marker], "note": inner, "public": "outer"}
        if reverse:
            record = dict(reversed(record.items()))
        for name, text, decode in representations(json.dumps(record)):
            if name == "json3":
                # record already contains one JSON-string document; three
                # additional wrappers require four JSON decodes, beyond the
                # unchanged three-layer budget. Other tables cover json3.
                continue
            output, findings = safety.redact_text(text)
            parsed = json.loads(decode(output))
            assert parsed["password"] == "[REDACTED]", (reverse, name, output)
            assert json.loads(parsed["note"]) == {"password": "[REDACTED]", "public": "inner"}
            assert parsed["public"] == "outer"
            assert marker not in output
            assert findings == [{"kind": "sensitive_field", "count": 2}]
            assert safety.redact_text(output) == (output, [])


def test_incomplete_json_does_not_shorten_a_sensitive_container_at_a_guessed_close():
    marker = "Structured_" + "Synthetic_7319"
    for head in ('"password": ["head"]', '{"password": ["head"]', '{"password": {"public":"head"}'):
        for name, text, _ in representations(head + marker + "}"):
            output, findings = safety.redact_text(text)
            assert marker not in output, (name, output)
            assert findings and safety.redact_text(output) == (output, [])


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("shape", ["string", "array", "object", "number", "literals"])
def test_mixed_json_percent_har_keeps_its_actual_layer_and_public_siblings(reverse, shape):
    marker = "MixedHar_" + "Synthetic_7319"
    values = {
        "string": [marker],
        "array": [[marker]],
        "object": [{"inner": marker}],
        "number": [7319],
        "literals": [None, True, False],
    }[shape]
    for value in values:
        record = {"name": "password", "value": value, "public": "inner"}
        if reverse:
            record = dict(reversed(record.items()))
        plain = json.dumps({"entries": [record], "public": "visible"})
        for percent_layers, json_layers in ((1, 1), (2, 1), (3, 1), (1, 2), (1, 3)):
            encoded = plain
            for _ in range(percent_layers):
                encoded = quote(encoded, safe="")
            text = json.dumps({"note": encoded, "public": "outer"})
            for _ in range(json_layers - 1):
                text = json.dumps(text)
            output, findings = safety.redact_text(text)
            parsed = output
            for _ in range(json_layers):
                parsed = json.loads(parsed)
            assert parsed["public"] == "outer"
            decoded = parsed["note"]
            for _ in range(percent_layers):
                decoded = unquote(decoded)
            assert json.loads(decoded) == {
                "entries": [{"name": "password", "value": "[REDACTED]", "public": "inner"}],
                "public": "visible",
            }, (shape, reverse, percent_layers, json_layers, output)
            assert marker not in decoded
            assert findings == [{"kind": "sensitive_field", "count": 1}]
            assert safety.redact_text(output) == (output, [])


def test_mixed_har_benign_mirrors_and_url_data_keep_their_boundaries():
    for plain in (
        json.dumps({"entries": [{"name": "theme", "value": "light"}], "public": "visible"}),
        "https://example.invalid/?q="
        + quote(json.dumps({"name": "sid", "value": "public"}), safe=""),
    ):
        text = json.dumps({"note": quote(plain, safe=""), "public": "outer"})
        assert safety.redact_text(text) == (text, [])


def test_minified_scalar_and_container_records_keep_independent_public_fields():
    for value in (7319, [1, 2], [], {}, None, True, False):
        plain = json.dumps({"password": value, "public": "visible"}, separators=(",", ":"))
        expected = value if value is None or type(value) is bool or value == {} else "[REDACTED]"
        for name, text, decode in representations(plain):
            output, findings = safety.redact_text(text)
            assert json.loads(decode(output)) == {"password": expected, "public": "visible"}, (
                name,
                value,
                output,
            )
            assert bool(findings) is (value != expected)
            assert safety.redact_text(output) == (output, [])
        output, findings = safety.redact_text(plain + "\n" + plain)
        assert [json.loads(line) for line in output.splitlines()] == [
            {"password": expected, "public": "visible"},
            {"password": expected, "public": "visible"},
        ]
        assert safety.redact_text(output) == (output, [])
