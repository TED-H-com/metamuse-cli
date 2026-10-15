"""Assignment, URL and XML extent regressions from the round-3 review."""

import random
import string
from urllib.parse import quote_plus

import pytest
from test_b16a_round4 import assert_private, marker


@pytest.mark.parametrize(
    "template",
    [
        'password=Ab3"{v} next',
        "password=Ab3'{v} next",
        "password=Ab3]{v} next",
        "password=Ab3}}{v} next",
        "<password><![CDATA[{v}]]></password>",
        "<wsse:Password>{v}</wsse:Password>",
        "cfg['password'] = '{v}'",
        'os.environ["API_TOKEN"] = "{v}"',
        "-token={v}",
        "-password {v}",
        'api_token: "str" = "{v}"',
        'https://user:Ab3"{v}@example.invalid/a',
        'https://example.invalid/?password=Ab3"{v}&format=json',
    ],
)
def test_r34a_syntax_and_extent_table(template):
    value = marker()
    assert_private(template.format(v=value), value)


@pytest.mark.parametrize(
    "shape", ["env", "kv", "flag", "form_first", "form_mid", "userinfo_raw", "url_query"]
)
def test_r34a_generated_full_punctuation_passwords(shape):
    rng = random.Random(1014)
    pool = string.ascii_letters + string.digits + string.punctuation
    for _ in range(300):
        while True:
            value = "".join(rng.choice(pool) for _ in range(16))
            if any(c in string.punctuation for c in value) and any(c.isdigit() for c in value):
                break
        if shape in {"userinfo_raw", "url_query"} and any(c in value for c in "/?#\\&"):
            continue  # Explicit URL grammar boundaries; report the excluded population.
        text = {
            "env": "DB_PASSWORD=" + value,
            "kv": "password=" + value,
            "flag": "tool --password=" + value + " --verbose",
            "form_first": "password=" + quote_plus(value) + "&username=bob",
            "form_mid": "username=bob&password=" + quote_plus(value) + "&scope=read",
            "userinfo_raw": "https://user:" + value + "@example.invalid/a",
            "url_query": "https://example.invalid/?password=" + value + "&view=1",
        }[shape]
        assert_private(text, value)


@pytest.mark.parametrize("delimiter", ["/", "?", "#", "\\", " "])
def test_r34a_whatwg_authority_boundary(delimiter):
    from metamuse.safety import redact_text

    text = "https://example.invalid" + delimiter + "user:public@data"
    assert redact_text(text) == (text, [])


def test_r34a_userinfo_uses_last_at():
    value = marker()
    assert_private(f"postgres://user:{value}@also-password@example.invalid/a", value)
