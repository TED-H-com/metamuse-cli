"""Placeholder boundaries are whole values, including nested shell expansion."""

import pytest
from test_b16a_round4 import assert_private, marker

from metamuse._muse_lifecycle import _check_question
from metamuse.safety import redact_text


@pytest.mark.parametrize("template", ["password=<Ab3>Zq{v}", "password=${{A}}{v}${{B}}"])
def test_r37_placeholder_lookalike_is_private(template):
    value = marker()
    assert_private(template.format(v=value), value)


@pytest.mark.parametrize(
    "value",
    [
        "${A:-${B}}",
        "${A:-${B:-${C}}}",
        "<your-password>",
        "${VALUE}",
        "$VAR",
        "%VAR%",
        "{{VALUE}}",
        "{name}",
        "{}",
        "%s",
    ],
)
def test_r37_whole_placeholder_exemptions(value):
    text = "password=" + value
    assert redact_text(text) == (text, [])
    _check_question(text)
