"""Serial incremental CPU and fresh-child RSS evidence, with source identity."""

import argparse
import hashlib
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

from b16a_r3_complexity import cpu_samples
from b16a_r4_complexity import GROWTH_SIZES
from b16a_r8_complexity import build, complexity_criterion, families

from metamuse import safety


def child(family, size):
    baseline = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    text = build(family, size).encode()[:size].decode(errors="ignore")
    started = time.process_time()
    safety.redact_text(text)
    return {
        "family": family,
        "size": len(text.encode()),
        "cpu": time.process_time() - started,
        "baseline_kib": baseline,
        "source": hashlib.sha256(Path(safety.__file__).read_bytes()).hexdigest(),
    }


def cap(family, size):
    result = subprocess.run(
        [sys.executable, __file__, "child", "--family", family, "--size", str(size)],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=True,
    )
    row = json.loads(result.stdout)
    row["rss_kib"] = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return row


def measure(mode, family):
    if mode == "cap":
        result = subprocess.run(
            [sys.executable, __file__, "driver", "--family", family],
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=True,
        )
        row = json.loads(result.stdout)
        row["accepted"] = row["rss_kib"] <= 1024 * 1024
        return row
    samples = []
    for size in (*GROWTH_SIZES, 1024 * 1024):
        text = build(family, size)
        safety.redact_text(text)
        samples.append(cpu_samples(text))
    accepted, slope, overall = complexity_criterion(samples[:4])
    return {
        "family": family,
        "sizes": [*GROWTH_SIZES, 1024 * 1024],
        "cpu": samples,
        "slope": slope,
        "overall": overall,
        "accepted": accepted,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["growth", "cap", "driver", "child"])
    parser.add_argument("--family")
    parser.add_argument("--size", type=int, default=20 * 1024 * 1024)
    args = parser.parse_args()
    if args.mode == "child":
        print(json.dumps(child(args.family, args.size)))
    elif args.mode == "driver":
        print(json.dumps(cap(args.family, args.size)))
    else:
        for family in [args.family] if args.family else families():
            print(json.dumps(measure(args.mode, family)), flush=True)
