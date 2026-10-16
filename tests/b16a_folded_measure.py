"""Affected header CPU growth and isolated child-RSS evidence with source binding."""

import argparse
import hashlib
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

from cpu_growth import SAMPLES, CpuRow, assert_cpu_growth, sample_cpu

from metamuse import safety

FAMILIES = ("cookie_fold", "quoted_fold", "json_fold", "exempt_headers", "url_cookie_pairs")
SIZES = (131072, 262144, 524288, 1048576)


def build(family, size):
    units = {
        "cookie_fold": "Cookie: a=one;\r\n b=two\nX-Public: public\n",
        "quoted_fold": 'Cookie: "a=one;"\n b="two"\nX-Public: public\n',
        "json_fold": json.dumps({"log": "Cookie: a=one;\r\n b=two\nX-Public: public"}) + "\n",
        "exempt_headers": 'Authorization: ""\n  Authorization: ""\n',
        "url_cookie_pairs": "https://example.invalid/x=one;y=two/path\n",
    }
    unit = units[family]
    return (unit * (size // len(unit) + 1))[:size]


def provenance():
    path = Path(safety.__file__).resolve()
    return {"module": str(path), "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def growth(family):
    rows = []
    for size in SIZES:
        text = build(family, size)
        rows.append(
            CpuRow(
                size,
                [
                    sample_cpu(lambda text=text: safety.redact_text(text), repeats=2)
                    for _ in range(SAMPLES)
                ],
            )
        )
    print(json.dumps({"family": family, **provenance()}), flush=True)
    assert_cpu_growth(rows, label="folded-" + family, ceiling=12)


def child(family, size):
    baseline = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    text = build(family, size)
    started = time.process_time()
    output, findings = safety.redact_text(text)
    return {
        "family": family,
        "size": len(text.encode()),
        "cpu": time.process_time() - started,
        "baseline_kib": baseline,
        "output_size": len(output),
        "findings": findings,
        **provenance(),
    }


def cap(family, size):
    result = subprocess.run(
        [sys.executable, __file__, "child", "--family", family, "--size", str(size)],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    )
    row = json.loads(result.stdout)
    row["rss_kib"] = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    row["accepted"] = row["cpu"] <= 300 and row["rss_kib"] <= 1048576
    assert row["accepted"], row
    return row


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("growth", "cap", "child"))
    parser.add_argument("--family", choices=FAMILIES, required=True)
    parser.add_argument("--size", type=int, default=20 * 1024 * 1024)
    args = parser.parse_args()
    if args.mode == "growth":
        growth(args.family)
    else:
        result = (
            child(args.family, args.size) if args.mode == "child" else cap(args.family, args.size)
        )
        print(json.dumps(result), flush=True)
