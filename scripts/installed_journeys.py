"""Provenance and subprocess supervision for the unchanged installed journeys."""

import hashlib
import json
import os
import resource
import shutil
import signal
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

HARNESS = (
    "tests/test_journey.py",
    "tests/test_muse_research.py",
    "tests/test_muse_lifecycle.py",
    "tests/test_safety_permits.py",
    "tests/fixtures/corpus/import/comments_tree.jsonl",
)
NODES = (
    "tests/test_journey.py::test_mcp_only_journey_followup_restart_and_export",
    "tests/test_muse_research.py::test_one_cli_approval_two_mcp_questions_restart_citations_export",
)


def source_identity(root: Path, expected: str) -> dict:
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

    commit = git("rev-parse", "HEAD")
    if commit != expected:
        raise ValueError("Runtime source commit differs from the requested SHA")
    if git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("Installed acceptance requires a clean checkout")
    return {
        "runtime_commit": commit,
        "runtime_tree": git("rev-parse", "HEAD^{tree}"),
        # Read the commit object so shallow checkout boundaries cannot hide parents.
        "runtime_parents": [
            line.split()[1]
            for line in git("cat-file", "-p", "HEAD").splitlines()
            if line.startswith("parent ")
        ],
        "candidate_sha": os.environ.get("CANDIDATE_SHA", commit),
        "event": os.environ.get("GITHUB_EVENT_NAME", "local"),
    }


def hashes(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    }


def verify_harness(root: Path, work: Path) -> dict[str, str]:
    expected = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in HARNESS}
    if hashes(work / "checks") != expected:
        raise ValueError("Copied harness/corpus differs from the exact source")
    return expected


def verify_package(root: Path, package: Path, prefix: Path) -> dict[str, str]:
    if not package.is_relative_to(prefix) or package.is_relative_to(root):
        raise ValueError("Imported package must be inside the fresh venv and outside the checkout")
    expected = hashes(root / "src/metamuse")
    expected.update(
        {f"data/skills/metamuse/{name}": h for name, h in hashes(root / "skills/metamuse").items()}
    )
    expected["data/THIRD_PARTY_NOTICES.md"] = hashlib.sha256(
        (root / "THIRD_PARTY_NOTICES.md").read_bytes()
    ).hexdigest()
    if hashes(package) != expected:
        raise ValueError("Installed package Python/data bytes differ from the exact source")
    return expected


def write_manifest(evidence: Path, data: dict) -> None:
    (evidence / "provenance.json").write_text(json.dumps(data, indent=2) + "\n")


def prepare(root: Path, work: Path, evidence: Path, expected: str) -> None:
    identity = source_identity(root, expected)
    for directory in (work, evidence):
        if directory.is_relative_to(root):
            raise ValueError("Scratch and evidence must be outside the checkout")
    evidence.mkdir(parents=True, exist_ok=True)
    data = {**identity, "nodes": NODES, "pytest_exit_code": None, "verified": False}
    write_manifest(evidence, data)
    for name in HARNESS:
        destination = work / "checks" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, destination)
    (work / "run").mkdir()
    data["harness_hashes"] = verify_harness(root, work)
    write_manifest(evidence, data)
    print(json.dumps(data, indent=2), flush=True)


def diagnostics(work: Path, group: int) -> dict:
    """Metadata only: no argv, file contents, SQL, rows, or environment values."""
    processes = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) == group:
                processes.append(
                    {
                        "pid": int(entry.name),
                        "state": fields[0],
                        "threads": int(fields[17]),
                        "fds": len(list((entry / "fd").iterdir())),
                        "wait_channel": (entry / "wchan").read_text().strip(),
                    }
                )
        except (OSError, ValueError):
            continue
    files = []
    roles = {"state.sqlite3": "database", "state.sqlite3-wal": "wal", "state.sqlite3-shm": "shm"}
    for path in work.glob("pytest/**/state.sqlite3*"):
        if path.name not in roles:
            continue
        try:
            info = path.lstat()
            files.append(
                {
                    "role": roles[path.name],
                    "mode": stat.S_IMODE(info.st_mode),
                    "regular": stat.S_ISREG(info.st_mode),
                    "nlink": info.st_nlink,
                    "inode": info.st_ino,
                    "bytes": info.st_size,
                }
            )
        except OSError:
            continue
        if len(files) == 100:
            break
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {
        "owned_processes": processes,
        "file_roles": files,
        "file_limit": 100,
        "child_usage": {
            "user_seconds": usage.ru_utime,
            "system_seconds": usage.ru_stime,
            "maxrss_kib": usage.ru_maxrss,
            "inblock": usage.ru_inblock,
            "outblock": usage.ru_oublock,
        },
    }


def run_pytest(python: str, work: Path, evidence: Path, data: dict) -> int:
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": ""}
    for name in ("PYTHONPATH", "PYTHONNOUSERSITE", "PYTEST_PLUGINS", "PYTEST_CURRENT_TEST"):
        env.pop(name, None)
    command = [
        python,
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
        "-o",
        "faulthandler_timeout=30",
        "--basetemp",
        str(work / "pytest"),
        "--junitxml",
        str(evidence / "junit.xml"),
        *[str(work / "checks" / node) for node in NODES],
    ]
    print("INSTALLED_PYTEST", json.dumps(command), flush=True)
    started = time.monotonic()
    process = subprocess.Popen(command, cwd=work / "run", env=env, start_new_session=True)
    try:
        result = process.wait()
        data["pytest_exit_code"] = result
        if result == 0:
            cases = ET.parse(evidence / "junit.xml").findall(".//testcase")
            expected = {
                tuple(node.split("/", 1)[1].replace(".py::", "::").split("::")) for node in NODES
            }
            observed = {
                (case.attrib["classname"].split(".")[-1], case.attrib["name"]) for case in cases
            }
            if len(cases) != 2 or observed != expected or any(list(case) for case in cases):
                raise ValueError(
                    "Installed acceptance requires both exact journeys to pass without skips"
                )
            data["summary"] = {"passed": 2, "failed": 0, "skipped": 0}
            print("INSTALLED_JOURNEYS_PASSED", json.dumps(data["summary"]), flush=True)
        return result if result >= 0 else 128 - result
    finally:
        data["runtime_seconds"] = time.monotonic() - started
        try:
            data["diagnostics"] = diagnostics(work, process.pid)
            if data.get("pytest_exit_code") != 0:
                logs = []
                for index, path in enumerate(sorted(work.glob("pytest/**/mcp-*.log"))[:20]):
                    if path.is_symlink() or not path.is_file():
                        continue
                    with path.open("rb") as handle:
                        handle.seek(0, os.SEEK_END)
                        size = handle.tell()
                        handle.seek(max(0, size - 65536))
                        target = f"mcp-stderr-{index}.log"
                        (evidence / target).write_bytes(handle.read())
                    logs.append({"artifact": target, "total_bytes": size, "tail_limit": 65536})
                data["mcp_stderr"] = logs
        finally:
            # Only this new session's group, including abandoned MCP children.
            # Diagnostic failure must not bypass owned-process cleanup.
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(process.pid, sig)
                except ProcessLookupError:
                    break
                if sig == signal.SIGTERM:
                    time.sleep(0.2)
            process.wait()
            write_manifest(evidence, data)


def run(root: Path, work: Path, evidence: Path, expected: str) -> int:
    import site

    import metamuse

    data = json.loads((evidence / "provenance.json").read_text())
    identity = source_identity(root, expected)
    if any(data[name] != value for name, value in identity.items()):
        raise ValueError("Source identity changed after wheel build")
    prefix = Path(sys.prefix).resolve()
    if prefix != work / "venv" or Path.cwd().is_relative_to(root) or site.ENABLE_USER_SITE:
        raise ValueError("Journeys require the isolated venv, user-site disabled and external cwd")
    origin = Path(metamuse.__file__).resolve()
    data["package_hashes"] = verify_package(root, origin.parent, prefix)
    data["harness_hashes"] = verify_harness(root, work)
    data.update(
        verified=True,
        package_origin=str(origin),
        interpreter=sys.version,
        executable=sys.executable,
        base_prefix=sys.base_prefix,
    )
    write_manifest(evidence, data)
    print(json.dumps(data, indent=2), flush=True)
    try:
        return run_pytest(sys.executable, work, evidence, data)
    finally:
        # Detect any harness/source/package mutation during execution as failure.
        verify_harness(root, work)
        verify_package(root, origin.parent, prefix)
        source_identity(root, expected)


def main() -> int:
    action, source, scratch, output, expected = sys.argv[1:]
    root, work, evidence = (Path(path).resolve() for path in (source, scratch, output))
    if evidence.is_relative_to(root):
        raise ValueError("Evidence must be outside the checkout")
    evidence.mkdir(parents=True, exist_ok=True)
    if action == "prepare":
        write_manifest(evidence, {"requested_commit": expected, "pytest_exit_code": None})

    def interrupted(signum, frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    result = 1
    try:
        if sys.version_info[:3] != (3, 12, 13):
            raise ValueError("Installed acceptance requires managed CPython 3.12.13")
        if action == "prepare":
            prepare(root, work, evidence, expected)
            result = 0
        elif action == "run":
            result = run(root, work, evidence, expected)
        else:
            raise ValueError("Unknown installed acceptance action")
        return result
    except SystemExit as exc:
        result = int(exc.code)
        raise
    finally:
        data = json.loads((evidence / "provenance.json").read_text())
        data["helper_exit_code"] = result
        data["stage"] = action
        write_manifest(evidence, data)


if __name__ == "__main__":
    raise SystemExit(main())
