"""Curl scope has local separator ownership; SQL principals cannot act backward."""

import pytest
from b16a_r8_cases import CURL, MARKER, private_cases, representations

from metamuse import safety
from metamuse._muse_lifecycle import _check_question
from metamuse.models import CoreError, ErrorCode


@pytest.mark.parametrize("family,text", list(private_cases()), ids=lambda x: x[:90])
def test_r8_confirmed_curl_scope_leaks_are_private(family, text):
    output, findings = safety.redact_text(text)
    assert MARKER not in output and findings, (family, text, output, findings)
    assert safety.redact_text(output)[0] == output
    with pytest.raises(CoreError) as caught:
        _check_question(text)
    assert caught.value.detail.code == ErrorCode.INPUT_INVALID


@pytest.mark.parametrize("prefix", ["the users' ", "r'\\' ", '"unclosed ', "rock 'n' roll "])
@pytest.mark.parametrize("representation", ["raw", "json1", "json2", "percent", "form"])
def test_r8_local_argument_quotes_ignore_prose_polarity(prefix, representation):
    text = prefix + f'{CURL} -H "X:A;B" -b {MARKER}'
    encoded = dict(representations(text))[representation]
    output, findings = safety.redact_text(encoded)
    assert MARKER not in output and findings, (encoded, output, findings)
    assert safety.redact_text(output)[0] == output


@pytest.mark.parametrize("separator", [";", "|", "&", "\n", "\r\n"])
@pytest.mark.parametrize("backslashes", [0, 2, 4])
def test_r8_even_backslashes_leave_real_separator_reset(separator, backslashes):
    text = f"{CURL} x" + chr(92) * backslashes + separator + " git -b public"
    # Isolate the owning scanner: unrelated local-path heuristics may mask '\\'.
    assert list(safety._curl_spans(text, safety._Syntax(text))) == []
    _check_question(text)


@pytest.mark.parametrize("flag", ["-u", "-b"])
@pytest.mark.parametrize("separator", [";", "|", "&"])
def test_r8_consumed_credential_still_releases_scope_at_real_separator(flag, separator):
    value = "alice:private" if flag == "-u" else "private"
    text = f"{CURL} {flag} {value}{separator} git -b public"
    output, findings = safety.redact_text(text)
    assert output.endswith(f"{separator} git -b public"), (text, output, findings)
    assert "[REDACTED]" in output


@pytest.mark.parametrize("delimiter", ['"', "'", "`", "$'"])
@pytest.mark.parametrize("separator", [";", "|", "&"])
def test_r8_quoted_argument_releases_scope_before_true_separator(delimiter, separator):
    text = f"{CURL} -H {delimiter}X:A;B{delimiter[-1]}{separator} git -b public"
    assert safety.redact_text(text) == (text, [])


@pytest.mark.parametrize("representation", ["raw", "json1", "json2", "percent", "form"])
@pytest.mark.parametrize("backslashes", [2, 4])
def test_r8_encoded_even_escape_parity_remains_a_real_reset(representation, backslashes):
    text = f"{CURL} x" + chr(92) * backslashes + "; git -b public"
    encoded = dict(representations(text))[representation]
    assert safety.redact_text(encoded) == (encoded, [])


@pytest.mark.parametrize("clause", ["IDENTIFIED BY", "with password"])
@pytest.mark.parametrize("principal", ["USER", "role", "LOGIN"])
def test_r8_later_sql_principal_cannot_activate_earlier_clause(clause, principal):
    text = f"CREATE {clause} '{MARKER}' {principal} alice"
    assert safety.redact_text(text) == (text, [])
    _check_question(text)


@pytest.mark.parametrize("statement", ["CREATE", "alter", "GRANT", "set"])
def test_r8_sql_order_is_forward_and_semicolon_bounded(statement):
    first = f"{statement} IDENTIFIED BY 'public' USER alice"
    second = f"{statement} USER bob IDENTIFIED BY '{MARKER}'"
    text = first + "; " + second + "; WITH PASSWORD 'public'"
    output, findings = safety.redact_text(text)
    assert output.startswith(first + "; ") and output.endswith("; WITH PASSWORD 'public'")
    assert MARKER not in output and findings


@pytest.mark.parametrize("flag", ["-b", "-u"])
@pytest.mark.parametrize("separator", [";", "|", "&"])
def test_r8_owned_shell_default_is_never_clipped_at_its_separator(flag, separator):
    value = "${A:-one" + separator + MARKER + "}"
    text = f"{CURL} {flag} " + ("alice:" if flag == "-u" else "") + value
    output, findings = safety.redact_text(text)
    assert MARKER not in output and findings
    assert safety.redact_text(output)[0] == output


@pytest.mark.parametrize("separator", [";", "|", "&"])
def test_r8_exempt_lookup_argument_separator_is_owned(separator):
    text = f'{CURL} -b os.getenv("public{separator}message") -b {MARKER}'
    output, findings = safety.redact_text(text)
    assert MARKER not in output and findings
    assert safety.redact_text(output)[0] == output


@pytest.mark.parametrize("value", ['os.getenv("public;message")', "[REDACTED]", "${C:-}"])
def test_r8_exempt_value_protection_stops_before_external_separator(value):
    text = f"{CURL} -b {value}; git -b public"
    assert safety.redact_text(text) == (text, [])


@pytest.mark.parametrize("flag", ["-u", "-b"])
@pytest.mark.parametrize("separator", [";", "|", "&"])
def test_r8_scope_reset_never_exposes_selected_credential_tail(flag, separator):
    first = f"{'HEAD'}_PRIVATE_SYNTHETIC_416328"
    last = f"{'TAIL'}_PRIVATE_SYNTHETIC_517439"
    value = ("alice:" if flag == "-u" else "") + first + separator + last
    text = f"{CURL} {flag} {value} git -b public"
    output, findings = safety.redact_text(text)
    assert first not in output and last not in output, (text, output)
    assert output.endswith(f"[REDACTED]{separator}[REDACTED] git -b public")
    assert {r["kind"] for r in findings} == {"sensitive_field" if flag == "-u" else "cookie"}
    assert safety.redact_text(output) == (output, [])
