"""Fresh-process, one-call CPU and child peak-RSS measurements (Linux KiB)."""

import argparse
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

from b16a_r4_complexity import build


def measure(family, size):
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--driver", family, str(size)],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    )
    return json.loads(result.stdout)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--driver", action="store_true")
    parser.add_argument("--child", action="store_true")
    parser.add_argument("family")
    parser.add_argument("size", type=int)
    args = parser.parse_args()
    if args.driver:
        result = subprocess.run(
            [sys.executable, __file__, "--child", args.family, str(args.size)],
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=True,
        )
        row = json.loads(result.stdout)
        row["rss_kib"] = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        print(json.dumps(row))
    else:
        from metamuse import safety

        baseline_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        text = build(args.family, args.size)
        started = time.process_time()
        output, findings = safety.redact_text(text)
        print(
            json.dumps(
                {
                    "baseline_kib": baseline_kib,
                    "family": args.family,
                    "size": len(text),
                    "cpu": time.process_time() - started,
                    "module": safety.__file__,
                    "output_size": len(output),
                    "findings": findings,
                }
            )
        )
