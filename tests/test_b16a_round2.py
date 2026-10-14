"""Independent B16a review regressions; synthetic OFFLINE evidence only."""

import signal
import time

import pytest

from metamuse import safety


def bounded_redaction(text, budget):
    def expired(*args):
        pytest.fail("Redaction exceeded its measured complexity budget")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, budget)
    started = time.perf_counter()
    try:
        result = safety.redact_text(text)
        assert time.perf_counter() - started < budget
        return result
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@pytest.mark.parametrize("case", ["unicode", "quotes"])
def test_f1_json_key_complexity(case):
    text = '"' + (r"\u0061" * 26 if case == "unicode" else r'\"' * 24575)
    assert bounded_redaction(text, 0.4) == (text, [])


@pytest.mark.parametrize("prefix", ["", "Cookie: ", "Set-Cookie: "])
@pytest.mark.parametrize("session_first", [False, True])
def test_f2_cookie_chain_keeps_all_members_private(prefix, session_first):
    marker = f"csrf_{session_first}_MARKER_SYNTHETIC_ONLY"
    pairs = ["theme=dark", "hatch_sess=S1", f"hatch_csrf={marker}"]
    if session_first:
        pairs.reverse()
    text = prefix + "; ".join(pairs) + "; Path=/; SameSite=Lax; HttpOnly; Secure"
    output, counts = safety.redact_text(text)
    assert marker not in output and "S1" not in output
    assert any(record["kind"] == "cookie" for record in counts)
    assert safety.redact_text(output) == (output, [])
