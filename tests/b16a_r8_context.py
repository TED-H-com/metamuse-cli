"""Round 7's complete both-placement product plus confirmed curl neighbours."""

import argparse
import json

from b16a_r7_context import NATURAL as NATURAL
from b16a_r7_context import SEED as SEED
from b16a_r7_context import cases as prior_cases
from b16a_r7_context import rows as prior_rows
from b16a_r7_context import verify as prior_verify
from b16a_r8_cases import rows as added_rows


def rows():
    yield from prior_rows()
    yield from added_rows()


def cases(*, full=False, seed=SEED):
    return prior_cases(full=full, seed=seed, pool=list(rows()))


def verify(*, full=False, seed=SEED, shard=0, shards=1):
    return prior_verify(full=full, seed=seed, shard=shard, shards=shards, pool=list(rows()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--shards", type=int, default=1)
    args = parser.parse_args()
    print(json.dumps(verify(**vars(args))), flush=True)
