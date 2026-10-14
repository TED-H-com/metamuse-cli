"""Run the independent B13 review mutants and round-6/7 removals, one pytest at a time."""

import argparse
import json
import subprocess
from pathlib import Path

CASES = {
    "M1": ["tests/test_facebook_round5.py", "-k", "network_blocks_page_websocket_bypasses"],
    "M2": ["tests/test_facebook_round5.py", "-k", "r5_n3"],
    "M3": ["tests/test_facebook_round5.py", "-k", "r5_n6"],
    "M4": ["tests/test_facebook_round5.py", "-k", "r5_12"],
    "M5": ["tests/test_facebook_round5.py", "-k", "r5_n4"],
    "M6": ["tests/test_facebook_review.py", "-k", "finding_04"],
    "M7": ["tests/test_facebook_guard_failures.py", "-k", "popup_close_exception"],
    "M8": ["tests/test_facebook_xhr.py"],
    "M9": ["tests/test_facebook_round6.py", "-k", "barrier_markers"],
    "F1": ["tests/test_facebook_popups.py"],
    "F2": ["tests/test_facebook_round6.py", "-k", "barrier_markers"],
    "F3": ["tests/test_facebook_round6.py", "-k", "policy_precedes"],
    "GUARD": [
        "tests/test_facebook_guard_failures.py",
        "-k",
        "worker_shutdown or disconnected_browser",
    ],
    "G1": ["tests/test_facebook_byte_stop.py", "-k", "closes_registered_sockets"],
}

CASES.update(
    {
        "NM1": ["tests/test_facebook_popups.py", "-k", "popup_creation_refused"],
        "NM2": ["tests/test_facebook_popups.py", "-k", "missing_popup_control"],
        "NM3": ["tests/test_facebook_visibility.py", "-k", "page_cannot_hide"],
        "NM4": ["tests/test_facebook_byte_stop.py", "-k", "connecting_upstream"],
        "NM5": ["tests/test_facebook_round6.py", "-k", "policy_precedes or denied_navigation"],
        "NM6": ["tests/test_facebook_guard_failures.py", "-k", "worker_shutdown"],
        "NM7": ["tests/test_facebook_popups.py", "-k", "iframe_only"],
        "R7F1": ["tests/test_facebook_visibility.py", "-k", "visible-child"],
        "R7F2": ["tests/test_facebook_target_shutdown.py", "-k", "false_close_waits"],
        "R7F3": ["tests/test_facebook_cutoff_precedence.py", "-k", "fatal_stop_identical"],
    }
)

OPTIONAL_CASES = {
    "G1_WATCH": [
        "tests/test_facebook.py::test_playwright_egress_proxy_byte_budget_stops_oversized_response"
    ],
}
ALL_CASES = {**CASES, **OPTIONAL_CASES}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Disk log directory outside the repository")
    parser.add_argument("--only", choices=list(ALL_CASES), nargs="+")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = {}
    for name in args.only or CASES:
        command = [
            "pytest-disktmp",
            "env",
            "UV_OFFLINE=1",
            "METAMUSE_REQUIRE_BROWSER_TESTS=1",
            "PYTHONPATH=tests",
            "PYTEST_PLUGINS=facebook_mutants,facebook_round7_mutants",
            (
                f"METAMUSE_FACEBOOK_ROUND7_MUTANT={name}"
                if name.startswith(("NM", "R7"))
                else f"METAMUSE_FACEBOOK_MUTANT={name}"
            ),
            "uv",
            "run",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            *ALL_CASES[name],
        ]
        with (args.output / f"{name}.log").open("w") as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
        output = (args.output / f"{name}.log").read_text()
        failures = [line for line in output.splitlines() if line.startswith("FAILED ")]
        killed = result.returncode == 1 and bool(failures)
        results[name] = {
            "result": "KILLED" if killed else "NOT_KILLED",
            "returncode": result.returncode,
            "failures": failures,
            "command": command,
        }
        print(f"{name}: {results[name]['result']} ({len(failures)} killing tests)", flush=True)
        (args.output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    return int(any(row["result"] != "KILLED" for row in results.values()))


if __name__ == "__main__":
    raise SystemExit(main())
