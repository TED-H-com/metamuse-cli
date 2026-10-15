"""Adversarial assignment grammars: CPU growth through 1 MiB."""

import json
import time

import pytest

from metamuse._muse_redact import _syntax_spans, sanitize_text
from metamuse.muse import _Secrets

FAMILIES = ["k:", "k=>", "a=1;", "k:v|", ";p=1", "authorization:"]


def measure(call, family, wrapped):
    rows = []
    for size in (131072, 262144, 524288, 1048576):
        value = (family * (size // len(family) + 1))[:size]
        if wrapped:
            value = json.dumps({"reply": value})
        start = time.process_time()
        call(value)
        cpu = time.process_time() - start
        rows.append(cpu)
        print(
            f"r6-growth call={call.__name__} family={family} json={wrapped} "
            f"bytes={size} cpu={cpu:.6f}",
            flush=True,
        )
        assert cpu < 12, rows
        if len(rows) > 1:
            assert rows[-1] / rows[-2] <= 2.5, rows
    return rows


def assert_linear(rows):
    assert all(b / a <= 2.5 for a, b in zip(rows, rows[1:], strict=False)), rows
    # 1 MiB ceiling allows >3x the observed CPU on this busy host; independent
    # of elapsed scheduling time. It prevents minute-long blocking regressions.
    assert rows[-1] < 12, rows


@pytest.mark.parametrize(
    "family,wrapped,entry",
    [
        pytest.param(
            family,
            wrapped,
            entry,
            marks=pytest.mark.xfail(
                strict=True,
                reason=(
                    "B16a core change request: safety.redact_text cookie regex "
                    "is quadratic for k=> runs"
                ),
            )
            if family == "k=>" and entry != "owned"
            else (),
        )
        for entry in ("owned", "text", "body")
        for wrapped in (False, True)
        for family in FAMILIES
    ],
)
def test_r6_2_adversarial_grammars_linear(family, wrapped, entry):
    call = (
        (lambda text: list(_syntax_spans(text)))
        if entry == "owned"
        else sanitize_text
        if entry == "text"
        else lambda text: _Secrets().body(text.encode())
    )
    rows = measure(call, family, wrapped)
    assert_linear(rows)
