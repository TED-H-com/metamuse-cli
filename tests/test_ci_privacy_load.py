"""Probe the real hosted shell loop with fake tools, never performance workloads."""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def measurement_command():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    step = workflow.split("- name: Run five serial final-tree privacy measurements\n")[1]
    return textwrap.dedent(step.split("run: |\n")[1].split("      - name:")[0])


@pytest.mark.parametrize("fail_run", [None, 3], ids=["five-complete", "stop-on-third-failure"])
def test_hosted_measurements_stop_on_failure_and_clean_each_disk_temp(tmp_path, fail_run):
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir()
    fake = tmp_path / "fake-uv"
    calls_path = tmp_path / "calls.json"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\nfrom pathlib import Path\n"
        f"calls_path = Path({str(calls_path)!r})\n"
        "args = sys.argv[1:]\n"
        "if '-m' not in args:\n"
        "    print(json.dumps({'evidence_class': 'SYNTHETIC_TOOL_PROBE'}))\n"
        "    sys.exit(0)\n"
        "calls = json.loads(calls_path.read_text()) if calls_path.exists() else []\n"
        "calls.append(args)\ncalls_path.write_text(json.dumps(calls))\n"
        "print('SYNTHETIC_TOOL_PROBE: no pytest or product workload executed', flush=True)\n"
        f"sys.exit(7 if len(calls) == {fail_run!r} else 0)\n"
    )
    fake.chmod(0o700)
    # A shell function keeps the fake tool ahead of the workflow's PATH setup.
    command = 'uv() { "$FAKE_UV" "$@"; }\n' + measurement_command()
    syntax = subprocess.run(["bash", "-n", "-c", command], capture_output=True, text=True)
    assert syntax.returncode == 0, syntax.stderr
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", command],
        cwd=tmp_path,
        env={**os.environ, "RUNNER_TEMP": str(runner_temp), "FAKE_UV": str(fake)},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == (7 if fail_run else 0), result.stdout + result.stderr
    calls = json.loads(calls_path.read_text())
    attempted = fail_run or 5
    assert len(calls) == attempted
    basetemps = [Path(args[args.index("--basetemp") + 1]) for args in calls]
    assert len(set(basetemps)) == attempted
    assert all(path.parent == runner_temp and not path.exists() for path in basetemps)
    nodes = calls[0][-6:]
    assert len(set(nodes)) == 6
    assert all(args[-6:] == nodes for args in calls)
    assert all(
        args[:9] == ["run", "--locked", "python", "-u", "-X", "faulthandler", "-m", "pytest", "-q"]
        for args in calls
    )
    evidence = runner_temp / "privacy-load-evidence"
    results = (evidence / "results.tsv").read_text().splitlines()
    completed = [row for row in results if "\tcompleted\t" in row]
    assert completed == [f"{run}\tcompleted\t0" for run in range(1, fail_run or 6)]
    assert results[-1] == f"{attempted}\tjob_exit\t{result.returncode}"
    assert sorted(path.name for path in evidence.glob("load-*.log")) == [
        f"load-{run}.log" for run in range(1, attempted + 1)
    ]
    assert all("SYNTHETIC_TOOL_PROBE" in path.read_text() for path in evidence.glob("load-*.log"))
