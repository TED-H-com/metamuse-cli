"""CPU doubling checks for 128 KiB through 1 MiB; no wall-time assertions."""

import time
import pytest
from metamuse._muse_redact import sanitize_text
from metamuse.muse import _Secrets
from metamuse.safety import redact_text


def growth(call, pair):
    previous = None
    rows = []
    for size in (131072, 262144, 524288, 1048576):
        text = (pair * (size // len(pair) + 1))[:size]
        start = time.process_time()
        call(text)
        cpu = time.process_time() - start
        rows.append((size, cpu))
        print(
            f"redact {call.__name__} pair={pair} bytes={size} cpu={cpu:.6f} ratio={cpu / previous if previous else 0:.3f}",
            flush=True,
        )
        if previous is not None:
            assert cpu / previous <= 2.5, rows
        previous = cpu


@pytest.mark.parametrize("entry", ["text", "body"])
def test_r5_5_sensitive_pairs_linear(entry):
    secrets = _Secrets()
    call = sanitize_text if entry == "text" else lambda text: secrets.body(text.encode())
    growth(call, "notary_token=x&")


@pytest.mark.xfail(
    strict=True,
    reason="Core change request: safety.redact_text is quadratic for ordinary k=v& pairs; remove this strict xfail when B16a makes it linear",
)
def test_r5_5_ordinary_pairs_core_growth():
    growth(redact_text, "vm_id=v1&")
