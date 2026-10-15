"""Small helper probes only; the installed acceptance journeys run in hosted CI."""

import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import installed_journeys as installed  # noqa: E402


def test_wrong_requested_source_is_refused():
    with pytest.raises(ValueError, match="source commit"):
        installed.source_identity(ROOT, "0" * 40)


@pytest.mark.parametrize("damage", ["harness", "corpus", "conftest", "package", "origin"])
def test_byte_and_origin_checks_reject_changed_inputs(tmp_path, damage):
    source = tmp_path / "source"
    work = tmp_path / "work"
    for name in installed.HARNESS:
        (work / "checks" / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, work / "checks" / name)
    installed.verify_harness(ROOT, work)
    package_source = source / "src/metamuse"
    package_source.mkdir(parents=True)
    (package_source / "sample.py").write_text("PUBLIC_SYNTHETIC = True\n")
    (package_source / "data").mkdir()
    (package_source / "data/schema.json").write_text("{}\n")
    (source / "THIRD_PARTY_NOTICES.md").write_text("Synthetic notices\n")
    skill = source / "skills/metamuse"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("Synthetic Skill\n")
    prefix = work / "venv"
    package = prefix / "site-packages/metamuse"
    shutil.copytree(package_source, package)
    shutil.copytree(skill, package / "data/skills/metamuse")
    shutil.copyfile(source / "THIRD_PARTY_NOTICES.md", package / "data/THIRD_PARTY_NOTICES.md")
    installed.verify_package(source, package, prefix)
    if damage in {"harness", "corpus", "conftest"}:
        relative = {
            "harness": "test_journey.py",
            "corpus": "fixtures/corpus/import/comments_tree.jsonl",
            "conftest": "conftest.py",
        }[damage]
        (work / "checks/tests" / relative).write_text("altered synthetic input\n")
        with pytest.raises(ValueError, match="harness/corpus"):
            installed.verify_harness(ROOT, work)
    else:
        if damage == "package":
            (package / "sample.py").write_text("PUBLIC_SYNTHETIC = False\n")
        with pytest.raises(ValueError, match="Installed package|Imported package"):
            installed.verify_package(source, package, source if damage == "origin" else prefix)


@pytest.mark.parametrize("exit_code,skip", [(0, False), (1, False), (139, False), (0, True)])
def test_fake_tool_preserves_failures_and_requires_two_passes(
    tmp_path, monkeypatch, exit_code, skip
):
    # This executable records invocation and emits synthetic JUnit. It never
    # imports pytest/metamuse or executes either acceptance harness.
    work = tmp_path / "work"
    (work / "run").mkdir(parents=True)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    fake = tmp_path / "fake-python"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\nfrom pathlib import Path\n"
        f"evidence = Path({str(evidence)!r})\n"
        "(evidence / 'invocation.json').write_text(json.dumps({'args':sys.argv[1:], "
        "'env':{key:value for key,value in os.environ.items() if key.startswith('PYTEST') "
        "or key in ('PYTHONPATH','PYTHONNOUSERSITE')}}))\n"
        "(evidence / 'junit.xml').write_text(\"<testsuites><testsuite>"
        "<testcase classname='tests.test_journey' "
        "name='test_mcp_only_journey_followup_restart_and_export'>"
        + ("<skipped/>" if skip else "")
        + "</testcase><testcase classname='tests.test_muse_research' "
        "name='test_one_cli_approval_two_mcp_questions_restart_citations_export'/>"
        f'</testsuite></testsuites>")\nsys.exit({exit_code})\n'
    )
    fake.chmod(0o700)
    monkeypatch.setenv("PYTHONPATH", "synthetic-unwanted-source")
    monkeypatch.setenv("PYTEST_PLUGINS", "synthetic_unwanted_plugin")
    data = {"pytest_exit_code": None}
    if skip:
        with pytest.raises(ValueError, match="without skips"):
            installed.run_pytest(str(fake), work, evidence, data)
    else:
        assert installed.run_pytest(str(fake), work, evidence, data) == exit_code
    assert json.loads((evidence / "provenance.json").read_text())["pytest_exit_code"] == exit_code
    invocation = json.loads((evidence / "invocation.json").read_text())
    args, env = invocation["args"], invocation["env"]
    assert args[:4] == ["-s", "-m", "pytest", "-q"]
    assert args[args.index("--basetemp") + 1] == str(work / "pytest")
    assert args[-2:] == [str(work / "checks" / node) for node in installed.NODES]
    assert "PYTHONPATH" not in env and "PYTEST_PLUGINS" not in env
    assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1" and env["PYTEST_ADDOPTS"] == ""


@pytest.mark.parametrize("result", ["success", "failure", "cancelled", "skipped", ""])
def test_required_gate_refuses_any_nonpassing_installed_job(tmp_path, result):
    # Execute the actual gate shell with a successful fake cohort verifier.
    # All cohort/static results succeed: installed failure must still fail it.
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    gate = workflow.split("- name: Require every job and exact full-suite runtime coverage\n")[1]
    command = textwrap.dedent(gate.split("run: |\n")[1])
    fake = tmp_path / "python3"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o700)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "GITHUB_SHA": "a" * 40,
        "COHORT_RESULT": "success",
        "STATIC_RESULT": "success",
        "INSTALLED_RESULT": result,
    }
    completed = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", command],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert (completed.returncode == 0) == (result == "success")
    jobs = dict(
        re.findall(
            r"^  ([a-z-]+):\n(.*?)(?=^  [a-z-]+:\n|\Z)", workflow.split("jobs:\n")[1], re.M | re.S
        )
    )
    installed_job = jobs["installed-journeys"]
    assert 'check_installed_journeys.sh "$GITHUB_SHA"' in installed_job
    assert "CANDIDATE_SHA: ${{ github.event.pull_request.head.sha || github.sha }}" in installed_job
    assert "if: always()\n        uses: actions/upload-artifact@" in installed_job
    assert "name: installed-${{ github.sha }}-journeys" in installed_job
    assert "pattern: ci-${{ github.sha }}-*" in jobs["check"]
