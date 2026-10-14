"""Independent B16a review regressions; synthetic OFFLINE evidence only."""

import signal
import time

import pytest
from test_muse_lifecycle import fixture_ctx as fixture_ctx
from test_muse_lifecycle import invoke

from metamuse import safety
from metamuse import models as m


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


def test_f5_topic_refusal_before_preview_or_state(fixture_ctx, monkeypatch):
    from metamuse._muse_lifecycle import _wire, prepare_open

    ctx = fixture_ctx
    marker = f"{ctx._frontend}_MARKER_SYNTHETIC_ONLY"
    request = m.MuseOpenRequest(
        project_id="missing", topic=f"hatch_sess={marker}", request_key="topic-refused"
    )
    # Freeze a body using the previous rules, then exercise the worker's second check.
    with monkeypatch.context() as old_rules:
        old_rules.setattr("metamuse._muse_lifecycle._check_question", lambda value: None)
        legacy = prepare_open(ctx, request)
    monkeypatch.setattr(ctx.store, "get", lambda *a, **kw: pytest.fail("Refusal must come first"))
    for call in (lambda: prepare_open(ctx, request), lambda: _wire(legacy)):
        with pytest.raises(m.CoreError) as error:
            call()
        assert error.value.detail.code == m.ErrorCode.INPUT_INVALID
        assert marker not in str(error.value)
    result = invoke(ctx, "open", request)
    assert result.error.code == m.ErrorCode.INPUT_INVALID and result.approval is None
    assert marker not in result.model_dump_json()
    with ctx.store.connection() as conn:
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM discussions").fetchone()[0] == 0
    assert ctx.muse_gateway.calls == []
