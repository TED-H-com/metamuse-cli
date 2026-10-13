"""Synthetic, redistributable redaction cases; no real credentials."""

import json

import pytest

from metamuse.safety import redact_text


@pytest.mark.parametrize(
    "text,secret,kind",
    [
        ("https://example.invalid/?access_token=FAKE_ACCESS&v=1", "FAKE_ACCESS", "url_token"),
        ("https://example.invalid/?auth_token=FAKE_AUTH", "FAKE_AUTH", "url_token"),
        ("https://example.invalid/?X-Amz-Signature=FAKE_AMZ", "FAKE_AMZ", "url_token"),
        ("https://example.invalid/?Signature=FAKE_SIGNED", "FAKE_SIGNED", "url_token"),
        ("https://example.invalid/?sig=FAKE_SIG", "FAKE_SIG", "url_token"),
        ("https://example.invalid/?access%5Ftoken=FAKE_ENCODED", "FAKE_ENCODED", "url_token"),
        ("https://example.invalid/?X-Goog-Credential=FAKE_GOOG", "FAKE_GOOG", "url_token"),
        ("Authorization: Bearer FAKE_BEARER", "FAKE_BEARER", "authorization"),
        ("Cookie: foo=FAKE_COOKIE; bar=second", "FAKE_COOKIE", "cookie"),
        ("Set-Cookie: session=FAKE_SET; HttpOnly; Secure", "FAKE_SET", "cookie"),
        ("foo=FAKE_PAIR; bar=second", "FAKE_PAIR", "cookie"),
        ("sessionid=FAKE_SESSION", "FAKE_SESSION", "session_cookie"),
        ("__Secure-session=FAKE_SECURE", "FAKE_SECURE", "session_cookie"),
        ("connect.sid=FAKE_CONNECT", "FAKE_CONNECT", "session_cookie"),
        ("sk-SYNTHETIC_ONLY_123456789", "SYNTHETIC_ONLY_123456789", "api_key"),
        ("read /tmp/synthetic/private.txt now", "/tmp/synthetic/private.txt", "local_path"),
        ('read "/synthetic path/private.txt" now', "/synthetic path/private.txt", "local_path"),
        (r"read C:\Synthetic\private.txt now", r"C:\Synthetic\private.txt", "local_path"),
        (r"read \\synthetic\share\private.txt now", r"\\synthetic\share\private.txt", "local_path"),
    ],
)
def test_redaction_covers_secret_categories_without_audit_values(text, secret, kind):
    output, records = redact_text(text)
    assert secret not in output and secret not in json.dumps(records)
    assert any(record["kind"] == kind for record in records)
    assert all(set(record) == {"kind", "count"} for record in records)
    assert redact_text(output)[0] == output


def test_redaction_preserves_zh_hant_emoji_newlines_and_public_urls():
    text = "繁體中文😀\r\nhttps://example.invalid/活動?q=繁中&n=2\ne\u0301"
    assert redact_text(text) == (text, [])


def test_redaction_handles_multiple_parameters_and_case():
    text = "https://example.invalid/a?sig=FAKE_ONE&v=2&AUTH_TOKEN=FAKE_TWO#keep"
    output, records = redact_text(text)
    assert output == "https://example.invalid/a?sig=[REDACTED]&v=2&AUTH_TOKEN=[REDACTED]#keep"
    assert records == [{"kind": "url_token", "count": 2}]
