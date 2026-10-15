"""Keep every orchestrator input, including the exact allowed D2 cut."""

import pytest
from b16a_r7_orchestrator import ROWS, V

from metamuse.safety import redact_text


@pytest.mark.parametrize("family,text", ROWS, ids=[name for name, _ in ROWS])
def test_r7_orchestrator_rows(family, text):
    out, found = redact_text(text)
    if text.startswith("'https://alice:pa',"):
        assert out == text and not found  # D2 raw owned closer + comma, before @.
    elif V in text:
        assert V not in out and found, (family, text, out, found)
    elif text in {"x = 'password=%s'%pw", 'x = "user=%s password=%s" % (u, p)'}:
        assert out != text and found  # D1/D2: the surrounding quote is unowned, or '%' is invalid.
    else:
        assert (out, found) == (text, []), (family, text, out, found)
    assert redact_text(out)[0] == out
