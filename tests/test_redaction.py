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


@pytest.mark.parametrize(
    "sample,secret",
    [
        ("https://example.invalid/cb#access_token=FAKE_FRAG", "FAKE_FRAG"),
        ("https://example.invalid/cb#id_token=FAKE_ID", "FAKE_ID"),
        ("https://example.invalid/?api_key=FAKE_API", "FAKE_API"),
        ("https://example.invalid/?apikey=FAKE_API", "FAKE_API"),
        ("https://example.invalid/?refresh_token=FAKE_REFRESH", "FAKE_REFRESH"),
        ("https://example.invalid/?client_secret=FAKE_CLIENT", "FAKE_CLIENT"),
        ("https://example.invalid/?secret=FAKE_SECRET", "FAKE_SECRET"),
        (
            "https://example.invalid/?next=https%3A%2F%2Fexample.invalid%2F%3Faccess_token%3DFAKE_ENC",
            "FAKE_ENC",
        ),
        ("https://example.invalid/?next=%3Fv%3D1%26sig%3DFAKE_ENC", "FAKE_ENC"),
        (
            "https://example.invalid/?sp=FAKE_SP&sv=FAKE_SV&sr=FAKE_SR&se=FAKE_SE&skoid=FAKE_ID",
            "FAKE_",
        ),
        ("https://example.invalid/?oh=FAKE_OH&oe=FAKE_OE", "FAKE_"),
        ("session%5Fid=FAKE_SESSION", "FAKE_SESSION"),
        ("Authorization: Basic FAKE_BASIC", "FAKE_BASIC"),
        ("Authorization: Token FAKE_TOKEN", "FAKE_TOKEN"),
        ('Authorization: Digest username="FAKE_USER", response="FAKE_RESPONSE"', "FAKE_"),
        ("Authorization: AWS4-HMAC-SHA256 Credential=FAKE_CREDENTIAL", "FAKE_CREDENTIAL"),
        ("[http] Cookie: xs=FAKE_XS", "FAKE_XS"),
        ("[http] Set-Cookie: datr=FAKE_DATR", "FAKE_DATR"),
        ('foo="FAKE QUOTED COOKIE"; bar=2', "FAKE QUOTED COOKIE"),
        ("Cookie: a=1;\r\n b=FAKE_FOLDED", "FAKE_FOLDED"),
        ('{"access_token":"FAKE_JSON"}', "FAKE_JSON"),
        ('{"Authorization":"Bearer FAKE_JSON"}', "FAKE_JSON"),
        ('{"Cookie":"xs=FAKE_JSON"}', "FAKE_JSON"),
        ('{"sessionid":"FAKE_JSON"}', "FAKE_JSON"),
        ('{"auth":"Authorization: Bearer FAKE_JSON"}', "FAKE_JSON"),
        ('{"cookie":"sessionid=FAKE_JSON"}', "FAKE_JSON"),
        ('{"path":"/tmp/synthetic.txt"}', "/tmp/synthetic.txt"),
    ],
)
def test_review_bypass_table_and_json_integrity(sample, secret):
    output, records = redact_text(sample)
    assert secret not in output and secret not in json.dumps(records)
    assert records and redact_text(output)[0] == output
    if sample.startswith("{"):
        json.loads(output)


@pytest.mark.parametrize(
    "text",
    [
        "年增率 5%/年",
        "費用（元）/人",
        "「台北」/「台中」",
        "請選擇：是 /否",
        "人數=100; 預算=200",
        "甲=一; 乙=二",
        "公園/學校",
        "繁中😀\r\n換行",
        '{"note":"5%/年", "人數":"100"}',
        "https://example.invalid/a/b?q=繁中",
    ],
)
def test_review_false_positive_table(text):
    assert redact_text(text) == (text, [])
