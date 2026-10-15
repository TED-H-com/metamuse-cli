"""Exercise the CI runner on tiny suites, including non-successful pytest exits."""

import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ci_cohorts as ci  # noqa: E402

SOURCE = "a" * 40


def run_probe(root, group, *args):
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/ci_cohorts.py"),
            "run",
            "--cohort",
            group,
            "--source-sha",
            SOURCE,
            "--manifest",
            str(root / f"{group}.json"),
            "--",
            "-q",
            "--basetemp",
            str(root / "pytest-temp"),
            *args,
        ],
        cwd=root,
        env={**os.environ, "PYTEST_ADDOPTS": "", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        capture_output=True,
        text=True,
        timeout=45,
    )


@pytest.fixture(scope="module")
def successful_manifests(tmp_path_factory):
    root = tmp_path_factory.mktemp("ci-cohort-probes")
    (root / "pytest.ini").write_text("[pytest]\ntestpaths = .\n")
    (root / "nested").mkdir()
    (root / "nested/widget_test.py").write_text(
        "import pytest\n@pytest.mark.parametrize('value', [1, 2])\n"
        "def test_future_pattern(value): assert value > 0\n"
    )
    for name in ("muse", "remote_media", "facebook", "privacy"):
        (root / f"test_{name}.py").write_text("def test_ok(): pass\n")
    # The real suite's conftest supplies the same ordering contract; the final
    # real-tree collection audit also checks the actual parity node and order.
    with (root / "test_facebook.py").open("a") as stream:
        stream.write("def test_r9_fixture_differential_corpus(): pass\n")
    (root / "conftest.py").write_text(
        "def pytest_collection_modifyitems(items):\n"
        "    items.sort(key=lambda i: i.name == 'test_r9_fixture_differential_corpus')\n"
    )
    for group in ci.COHORTS:
        result = run_probe(root, group)
        assert result.returncode == 0, result.stdout + result.stderr
    ci.verify_manifests(root, SOURCE)
    manifests = {group: json.loads((root / f"{group}.json").read_text()) for group in ci.COHORTS}
    assert len(manifests["core"]["selected"]) == 2  # nested *_test.py and both parameters
    assert len(manifests["core"]["full"]) == 7
    return manifests


@pytest.mark.parametrize(
    "damage",
    [
        "omitted_node",
        "duplicate_assignment",
        "full_manifest_mismatch",
        "missing_cohort",
        "failed_exit",
        "collection_only",
        "unfinished_node",
        "wrong_source",
        "parity_not_last",
    ],
)
def test_aggregate_refuses_incomplete_or_inconsistent_evidence(
    tmp_path, successful_manifests, damage
):
    data = copy.deepcopy(successful_manifests)
    if damage == "omitted_node":
        data["facebook"]["selected"].pop(0)
        data["facebook"]["finished"].pop(0)
    elif damage == "duplicate_assignment":
        data["media"]["selected"].append(data["core"]["selected"][0])
    elif damage == "full_manifest_mismatch":
        data["media"]["full"].append("nested/new_test.py::test_new")
        data["media"]["full"].sort()
        data["media"]["full_hash"] = ci.node_hash(data["media"]["full"])
    elif damage == "missing_cohort":
        del data["privacy"]
    elif damage == "failed_exit":
        data["muse"]["pytest_exit_code"] = 1
    elif damage == "collection_only":
        data["muse"]["collect_only"] = True
    elif damage == "unfinished_node":
        data["core"]["finished"].pop()
    elif damage == "wrong_source":
        data["core"]["source_sha"] = "b" * 40
    elif damage == "parity_not_last":
        data["facebook"]["selected"].reverse()
    for group, manifest in data.items():
        (tmp_path / f"{group}.json").write_text(json.dumps(manifest))
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/ci_cohorts.py"),
            "verify",
            "--manifest-dir",
            str(tmp_path),
            "--source-sha",
            SOURCE,
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0, damage
    assert "Coverage verification failed:" in result.stderr


@pytest.mark.parametrize(
    "group,body,args,exit_code",
    [
        ("invalid", "def test_ok(): pass\n", (), 2),
        ("core", "def test_failure(): assert False\n", ("--maxfail=1",), 1),
        ("core", "raise RuntimeError('synthetic collection failure')\n", (), 2),
        ("core", "def test_interrupt(): raise KeyboardInterrupt\n", (), 2),
        ("media", "def test_ok(): pass\n", (), 4),
        ("core", "def test_ok(): pass\n", ("-k", "absent"), 4),
        ("core", "", (), 4),
    ],
    ids=[
        "invalid-group",
        "failed-test",
        "collection-error",
        "keyboard-interrupt",
        "empty-selection",
        "refuse-deselection",
        "no-items",
    ],
)
def test_runner_preserves_nonpassing_exits(tmp_path, group, body, args, exit_code):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_probe.py").write_text(body)
    result = run_probe(tmp_path, group, *args)
    assert result.returncode == exit_code, result.stdout + result.stderr
    if group != "invalid":
        manifest = json.loads((tmp_path / f"{group}.json").read_text())
        assert manifest["pytest_exit_code"] == exit_code
    if exit_code == 1:
        assert "assert False" in result.stdout


def test_workflow_requires_all_cohorts_static_wheel_and_exact_coverage():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    jobs = dict(
        re.findall(
            r"^  ([a-z-]+):\n(.*?)(?=^  [a-z-]+:\n|\Z)", workflow.split("jobs:\n")[1], re.M | re.S
        )
    )
    assert set(jobs) == {"static-wheel", "cohorts", "installed-journeys", "check"}
    for job in jobs.values():
        assert "timeout-minutes: 30\n" in job
        assert "runs-on: ubuntu-24.04\n" in job
        assert (
            "uv sync --locked --python 3.12.13 --group dev --extra remote --extra facebook" in job
        )
        assert 'python-version: "3.12.13"' in job
        assert "assert sys.version_info[:3] == (3, 12, 13)" in job
    assert 'METAMUSE_REQUIRE_BROWSER_TESTS: "1"' in workflow.split("jobs:\n")[0]
    cohorts = jobs["cohorts"]
    assert "cohort: [core, muse, media, facebook, privacy]" in cohorts
    assert "fail-fast: false" in cohorts and "max-parallel: 5" in cohorts
    assert "--maxfail=1 -o faulthandler_timeout=30 --basetemp" in cohorts
    assert 'mktemp -d "${RUNNER_TEMP}/metamuse-pytest.XXXXXX"' in cohorts
    assert "trap 'rm -rf \"$pytest_temp_dir\"' EXIT" in cohorts
    assert "set -o pipefail" in cohorts
    assert "playwright install --with-deps chromium" in cohorts
    assert "kernel.apparmor_restrict_unprivileged_userns=0" in cohorts
    assert "if: always()\n        uses: actions/upload-artifact@" in cohorts
    assert "ci-${{ github.sha }}-${{ matrix.cohort }}" in cohorts
    check = jobs["check"]
    assert "needs: [static-wheel, cohorts, installed-journeys]\n    if: always()" in check
    assert "pattern: ci-${{ github.sha }}-*" in check
    assert 'verify --manifest-dir ci-evidence --source-sha "$GITHUB_SHA"' in check
    assert 'test "$COHORT_RESULT" = success' in check
    assert 'test "$STATIC_RESULT" = success' in check
    assert 'test "$INSTALLED_RESULT" = success' in check
    assert "if: always()\n        env:" in check
    assert "continue-on-error" not in workflow
    for action in re.findall(r"uses: (\S+)", workflow):
        assert re.fullmatch(r"(?:actions|astral-sh)/[\w-]+@[a-f0-9]{40}", action)
    static = jobs["static-wheel"]
    for command in (
        "ruff check .",
        "ruff format --check .",
        "docs/design/validate_design_pack.py",
        "python -m metamuse.schemas\n          git diff --exit-code",
        "tests/fixtures/corpus/check_corpus.py",
        "scripts/smoke_wheel.sh",
    ):
        assert command in static
    wheel = (ROOT / "scripts/smoke_wheel.sh").read_text()
    for contract in (
        "--offline",
        "--no-deps",
        "skill",
        "migrations/*.sql",
        "schemas/*.json",
        "session.list_tools()",
    ):
        assert contract in wheel
