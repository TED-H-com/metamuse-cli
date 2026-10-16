"""Affected families under the unchanged both-placement Round 8 contexts."""

import hashlib
import json
from collections import Counter
from pathlib import Path

from b16a_folded_headers import rows as added_rows
from b16a_r7_context import NATURAL, SEED, verify
from b16a_r8_context import rows as prior_rows

from metamuse import safety


def rows():
    yield from (
        row
        for row in prior_rows()
        if any(word in row.family.casefold() for word in ("cookie", "header", "authorization"))
    )
    yield from added_rows()


if __name__ == "__main__":
    pool = list(rows())
    families = Counter(row.family for row in pool)
    assert len(pool) == 942 and len(families) == 198, (len(pool), len(families))
    source = Path(safety.__file__).resolve()
    print(
        json.dumps(
            {
                "module": str(source),
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "rows": len(pool),
                "families": dict(families),
                "natural": len(NATURAL),
                "random_per_row": 8,
                "placements": 2,
                "seed": SEED,
            }
        ),
        flush=True,
    )
    result = verify(full=True, pool=pool)
    print(json.dumps(result), flush=True)
    assert result["counts"]["pairs"] == len(pool) * (len(NATURAL) + 8) * 2
    assert not result["failures"], result["failures"]
    assert result["counts"]["leaks"] == result["counts"]["nonfixpoint"] == 0
    assert result["counts"]["changed_N"] == 0
