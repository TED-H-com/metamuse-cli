"""Scoped form decoding and raw Bearer regressions."""

import base64
import random

import pytest
from test_b16a_round4 import assert_private, marker


@pytest.mark.parametrize("length", [24, 32, 48])
def test_r32_bearer_question_mark_seeded_rates(length):
    rng = random.Random(1014)
    tokens = [base64.b64encode(rng.randbytes(length)).decode() for _ in range(300)]
    assert any("+" in token for token in tokens)
    for template in (
        "Is this token still valid? Bearer {t}",
        "Calling /v1/items?limit=10 with Bearer {t} returns 401",
        "Bearer {t} is rejected, why?",
    ):
        for token in tokens:
            assert_private(template.format(t=token), token)


@pytest.mark.parametrize("body", ["q=--password+{v}", "q=curl+-u+user%3A{v}"])
def test_r32_whole_text_form_decoding(body):
    value = marker()
    assert_private(body.format(v=value), value)


def test_r32_question_mark_does_not_enable_form_syntax_in_prose():
    from metamuse.safety import redact_text

    text = "Why? discuss+--password+required"
    assert redact_text(text) == (text, [])


@pytest.mark.parametrize(
    "text",
    [
        "https://example.invalid/#?discuss+--password+required",
        "https://example.invalid/?q=hello#discuss+--password+required",
    ],
)
def test_r32_url_fragment_never_enables_form_decoding(text):
    from metamuse.safety import redact_text

    assert redact_text(text) == (text, [])
