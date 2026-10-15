"""Serial or sharded OFFLINE CPU/RSS evidence for all inherited families."""

import argparse
import json
import time

from b16a_r3_complexity import cpu_samples
from b16a_r4_complexity import GROWTH_SIZES, settle_samples
from b16a_r7_complexity import build, complexity_criterion, families
from b16a_r7_memory import measure

from metamuse.safety import redact_text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["growth", "cap", "settle"])
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--shards", type=int, default=1)
    args = parser.parse_args()
    for index, family in enumerate(families()):
        if index % args.shards != args.shard:
            continue
        if args.mode == "cap":
            row = measure(family, 20 * 1024 * 1024)
            row["accepted"] = row["rss_kib"] <= 1024 * 1024
        elif args.mode == "settle":
            row = {
                "family": family,
                "size": 49152 + 8192 + 2,
                "cpu": settle_samples(build(family, 49152 + 8192 + 2)),
            }
        else:
            samples = []
            for size in (*GROWTH_SIZES, 1024 * 1024):
                text = build(family, size)
                redact_text(text)
                samples.append(cpu_samples(text))
            accepted, slope, overall = complexity_criterion(samples[:4])
            row = {
                "family": family,
                "sizes": [*GROWTH_SIZES, 1024 * 1024],
                "cpu": samples,
                "slope": slope,
                "overall": overall,
                "accepted": accepted,
            }
        row["wall_timestamp"] = time.time()
        print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
