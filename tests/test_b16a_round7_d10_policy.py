"""Pin the approved lexical curl policy without claiming shell interpretation."""

import pytest

from metamuse._muse_lifecycle import _check_question
from metamuse.models import CoreError, ErrorCode
from metamuse.safety import redact_text

CURL = "cu" + "rl"
MARKER = f"{'D10'}_MARKER_SYNTHETIC_573921"


def test_d10_echo_curl_is_an_intentional_conservative_refusal():
    text = f"echo {CURL} -b public"
    assert redact_text(text) == (
        f"echo {CURL} -b [REDACTED]",
        [{"kind": "cookie", "count": 1}],
    )
    with pytest.raises(CoreError) as caught:
        _check_question(text)
    assert caught.value.detail.code == ErrorCode.INPUT_INVALID


@pytest.mark.parametrize(
    "text",
    [
        "Why did git checkout -b main fail?",
        "Run python -b to check BytesWarning.",
        "Which record is identified by that UUID?",
        "mktime()-based conversion",
        "len(a)-by-len(b) array",
        "# filesets are identified by four decimal values",
    ],
)
def test_d10_six_required_benign_examples_stay_unchanged(text):
    assert redact_text(text) == (text, [])
    _check_question(text)


@pytest.mark.parametrize(
    "prefix",
    [
        "",
        "echo ",
        "rock 'n' roll ",
        "the users' ",
        "MODE=fixture sudo env time command exec nohup ",
        "/usr/bin/",
    ],
)
@pytest.mark.parametrize("flag", ["-u", "-b"])
def test_d10_curl_token_establishes_conservative_scope(prefix, flag):
    value = f"alice:{MARKER}" if flag == "-u" else MARKER
    text = f"{prefix}{CURL} {flag} {value}"
    output, findings = redact_text(text)
    assert MARKER not in output
    assert {"kind": "sensitive_field" if flag == "-u" else "cookie", "count": 1} in findings
    assert redact_text(output)[0] == output
    with pytest.raises(CoreError) as caught:
        _check_question(text)
    assert caught.value.detail.code == ErrorCode.INPUT_INVALID


@pytest.mark.parametrize("separator", ["|", ";", "&&", "||", "\n"])
def test_d10_literal_shell_separator_resets_short_cookie_scope(separator):
    text = f"{CURL} {separator} git -b public"
    assert redact_text(text) == (text, [])
    _check_question(text)


@pytest.mark.parametrize("flag", ["--user", "--cookie"])
def test_d10_long_flags_remain_independent_of_curl_scope(flag):
    value = f"alice:{MARKER}" if flag == "--user" else MARKER
    text = f"git {flag} {value}"
    output, findings = redact_text(text)
    assert MARKER not in output and findings
    with pytest.raises(CoreError) as caught:
        _check_question(text)
    assert caught.value.detail.code == ErrorCode.INPUT_INVALID


@pytest.mark.parametrize(
    "statement,principal,clause",
    [
        ("CREATE", "USER", "IDENTIFIED BY"),
        ("alter", "role", "with password"),
        ("GRANT", "LOGIN", "IDENTIFIED BY"),
        ("SET", "USER", "WITH PASSWORD"),
    ],
)
def test_d10_sql_statement_with_earlier_principal_is_redacted(statement, principal, clause):
    text = f"{statement} {principal} alice {clause} '{MARKER}'"
    output, findings = redact_text(text)
    assert MARKER not in output and findings
    with pytest.raises(CoreError) as caught:
        _check_question(text)
    assert caught.value.detail.code == ErrorCode.INPUT_INVALID


@pytest.mark.parametrize(
    "text",
    [
        f"IDENTIFIED BY '{MARKER}'",
        f"WITH PASSWORD '{MARKER}'",
        f"CREATE demo IDENTIFIED BY '{MARKER}'",
        f"CREATE USER alice; IDENTIFIED BY '{MARKER}'",
        f"CREATE ROLE alice; WITH PASSWORD '{MARKER}'",
    ],
)
def test_d10_sql_clause_without_its_statement_anchor_is_unchanged(text):
    assert redact_text(text) == (text, [])
    _check_question(text)
