"""Repeated CPU growth checks; known core quadratic work uses smaller inputs."""

import pytest
from cpu_growth import SAMPLES, CpuRow, assert_cpu_growth, sample_cpu

from metamuse._muse_redact import sanitize_text
from metamuse.muse import _Secrets
from metamuse.safety import redact_text


def growth(call, pair):
    rows = []
    # Three complete quadratic samples through 1 MiB would take minutes. These
    # smaller inputs still start above 20 ms on the measured host and span 8x.
    sizes = (
        (8192, 16384, 32768, 65536) if pair == "vm_id=v1&" else (131072, 262144, 524288, 1048576)
    )
    repeats = 1 if pair == "vm_id=v1&" else 2
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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Core change request: safety.redact_text is quadratic for ordinary k=v& pairs; "
        "remove this strict xfail when B16a makes it linear"
    ),
)
def test_r5_5_ordinary_pairs_core_growth():
    growth(redact_text, "vm_id=v1&")
