"""Small helper probes only; the installed acceptance journeys run in hosted CI."""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET
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


def test_real_pytest_binds_junit_identity_to_copied_checks_root(tmp_path):
    # Only two tiny synthetic functions: neither real journey is copied/imported.
    work = tmp_path / "work"
    (work / "run").mkdir(parents=True)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    for node in installed.NODES:
        module, name = node.split("::")
        target = work / "checks" / module
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"def {name}():\n    pass\n")
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": ""}
    for name in ("PYTHONPATH", "PYTHONNOUSERSITE", "PYTEST_PLUGINS", "PYTEST_CURRENT_TEST"):
        env.pop(name, None)
    baseline = evidence / "baseline.xml"
    result = subprocess.run(
        [
            sys.executable,
            "-s",
            "-m",
            "pytest",
            "-q",
            "-c",
            "/dev/null",
            "--confcutdir",
            str(work / "checks"),
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(tmp_path / "baseline-temp"),
            "--junitxml",
            str(baseline),
            *[str(work / "checks" / node) for node in installed.NODES],
        ],
        cwd=work / "run",
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    cases = ET.parse(baseline).findall(".//testcase")
    assert len(cases) == 2 and all(case.attrib["classname"] == "" for case in cases)
    data = {"pytest_exit_code": None}
    assert installed.run_pytest(sys.executable, work, evidence, data) == 0
    cases = ET.parse(evidence / "junit.xml").findall(".//testcase")
    assert [(case.attrib["classname"], case.attrib["name"]) for case in cases] == [
        (module.removesuffix(".py").replace("/", "."), name)
        for module, name in (node.split("::") for node in installed.NODES)
    ]
    assert all(not list(case) for case in cases)
    assert data["summary"] == {"passed": 2, "failed": 0, "skipped": 0}


@pytest.mark.parametrize(
    "exit_code,damage",
    [
        (0, "none"),
        (1, "none"),
        (139, "none"),
        (0, "empty-class"),
        (0, "wrong-module"),
        (0, "wrong-name"),
        (0, "missing"),
        (0, "duplicate"),
        (0, "skipped"),
        (0, "failure"),
        (0, "error"),
        (0, "diagnostics"),
    ],
)
def test_fake_tool_preserves_failures_and_requires_two_passes(
    tmp_path, monkeypatch, exit_code, damage
):
    # Synthetic JUnit probes complement, rather than replace, the actual-pytest
    # rootdir regression above. No product acceptance harness executes here.
    work = tmp_path / "work"
    (work / "run").mkdir(parents=True)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    xml = ET.Element("testsuites")
    suite = ET.SubElement(xml, "testsuite")
    cases = [
        ET.SubElement(
            suite, "testcase", classname=module.removesuffix(".py").replace("/", "."), name=name
        )
        for module, name in (node.split("::") for node in installed.NODES)
    ]
    if damage == "empty-class":
        for case in cases:
            case.set("classname", "")
    elif damage == "wrong-module":
        cases[0].set("classname", "tests.wrong_module")
    elif damage == "wrong-name":
        cases[0].set("name", "test_wrong_node")
    elif damage == "missing":
        suite.remove(cases[0])
    elif damage == "duplicate":
        ET.SubElement(suite, "testcase", **cases[0].attrib)
    elif damage in ("skipped", "failure", "error"):
        ET.SubElement(cases[0], damage)
    fake = tmp_path / "fake-python"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\nfrom pathlib import Path\n"
        f"evidence = Path({str(evidence)!r})\n"
        "(evidence / 'invocation.json').write_text(json.dumps({'args':sys.argv[1:], "
        "'pid':os.getpid(), 'group':os.getpgrp(), "
        "'env':{key:value for key,value in os.environ.items() if key.startswith('PYTEST') "
        "or key in ('PYTHONPATH','PYTHONNOUSERSITE')}}))\n"
        f"(evidence / 'junit.xml').write_text({ET.tostring(xml, encoding='unicode')!r})\n"
        f"sys.exit({exit_code})\n"
    )
    fake.chmod(0o700)
    monkeypatch.setenv("PYTHONPATH", "synthetic-unwanted-source")
    monkeypatch.setenv("PYTEST_PLUGINS", "synthetic_unwanted_plugin")
    signals = []
    killpg = os.killpg

    def kill_owned_group(group, sig):
        signals.append((group, sig))
        killpg(group, sig)

    monkeypatch.setattr(installed.os, "killpg", kill_owned_group)
    if damage == "diagnostics":

        def failed_diagnostics(work, group):
            raise RuntimeError("synthetic diagnostics failure")

        monkeypatch.setattr(installed, "diagnostics", failed_diagnostics)
    data = {"pytest_exit_code": None}
    if damage == "diagnostics":
        with pytest.raises(RuntimeError, match="synthetic diagnostics failure"):
            installed.run_pytest(str(fake), work, evidence, data)
    elif damage != "none":
        with pytest.raises(ValueError, match="without skips"):
            installed.run_pytest(str(fake), work, evidence, data)
        assert "summary" not in data
    else:
        assert installed.run_pytest(str(fake), work, evidence, data) == exit_code
    assert json.loads((evidence / "provenance.json").read_text())["pytest_exit_code"] == exit_code
    invocation = json.loads((evidence / "invocation.json").read_text())
    assert signals and all(
        group == invocation["pid"] == invocation["group"] for group, _ in signals
    )
    assert signals[0][1] == signal.SIGTERM
    args, env = invocation["args"], invocation["env"]
    assert args[:4] == ["-s", "-m", "pytest", "-q"]
    assert args[args.index("--rootdir") + 1] == str(work / "checks")
    assert args[args.index("--confcutdir") + 1] == str(work / "checks")
    assert args[args.index("--basetemp") + 1] == str(work / "pytest")
    assert args[-2:] == [str(work / "checks" / node) for node in installed.NODES]
    assert "PYTHONPATH" not in env and "PYTEST_PLUGINS" not in env
    assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1" and env["PYTEST_ADDOPTS"] == ""


@pytest.mark.parametrize("result", ["success", "failure", "cancelled", "skipped", ""])
@pytest.mark.parametrize("required_job", ["INSTALLED_RESULT", "PRIVACY_LOAD_RESULT"])
def test_required_gate_refuses_any_nonpassing_installed_or_load_job(tmp_path, result, required_job):
    # Execute the actual gate shell with a successful fake cohort verifier.
    # All other jobs succeed: either additional required job must still fail it.
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
        "INSTALLED_RESULT": "success",
        "PRIVACY_LOAD_RESULT": "success",
        required_job: result,
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
