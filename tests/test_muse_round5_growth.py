"""Repeated CPU growth checks through 1 MiB."""

import pytest
from cpu_growth import SAMPLES, CpuRow, assert_cpu_growth, sample_cpu

from metamuse._muse_redact import sanitize_text
from metamuse.muse import _Secrets
from metamuse.safety import redact_text


def growth(call, pair):
    rows = []
    sizes = (131072, 262144, 524288, 1048576)
    repeats = 2
    for size in sizes:
        text = (pair * (size // len(pair) + 1))[:size]
        samples = [
            sample_cpu(lambda text=text: call(text), repeats=repeats) for _ in range(SAMPLES)
        ]
        rows.append(CpuRow(size, samples))
    assert_cpu_growth(rows, label=f"r5-{pair}-{call.__name__}")


@pytest.mark.parametrize("entry", ["text", "body"])
def test_r5_5_sensitive_pairs_linear(entry):
    secrets = _Secrets()
    call = sanitize_text if entry == "text" else lambda text: secrets.body(text.encode())
    growth(call, "notary_token=x&")


def test_r5_5_ordinary_pairs_core_growth():
    growth(redact_text, "vm_id=v1&")
