"""Adversarial assignment grammars: CPU growth through 1 MiB."""

import json

import pytest
from cpu_growth import SAMPLES, CpuRow, assert_cpu_growth, sample_cpu

from metamuse._muse_redact import _syntax_spans, sanitize_text
from metamuse.muse import _Secrets

FAMILIES = ["k:", "k=>", "a=1;", "k:v|", ";p=1", "authorization:"]


def measure(call, family, wrapped, entry):
    rows = []
    core_quadratic = family == "k=>" and entry != "owned"
    sizes = (4096, 8192, 16384, 32768) if core_quadratic else (131072, 262144, 524288, 1048576)
    # Fixed batch sizes keep the fastest grammar above 20 ms without changing
    # the amount of work per byte between sizes. Bound known core quadratic CPU.
    repeats = 1 if core_quadratic else 4 if family == "authorization:" else 2
    for size in sizes:
        value = (family * (size // len(family) + 1))[:size]
        if wrapped:
            value = json.dumps({"reply": value})
        samples = [
            sample_cpu(lambda value=value: call(value), repeats=repeats) for _ in range(SAMPLES)
        ]
        rows.append(CpuRow(size, samples))
    return rows


def assert_linear(rows, *, label):
    # Retain the absolute CPU ceiling as well as both sampled growth criteria.
    assert_cpu_growth(rows, label=label, ceiling=12)


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
    rows = measure(call, family, wrapped, entry)
    assert_linear(rows, label=f"r6-{entry}-{family}-json={wrapped}")
