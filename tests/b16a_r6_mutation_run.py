"""Isolated mutation execution with explicit library and helper import canaries."""

import argparse
import difflib
import json
import os
import shutil
import subprocess
from pathlib import Path


def main():
    from b16a_r3_mutations import MUTANTS, REPO

    parser = argparse.ArgumentParser()
    parser.add_argument("names", nargs="*")
    parser.add_argument("--root", type=Path, default=Path("/tmp/b16a-r6/mutations"))
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--shards", type=int, default=1)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    snapshot = args.root / ("snapshot-" + str(args.shard))
    if snapshot.exists():
        shutil.rmtree(snapshot)
    shutil.copytree(REPO / "src", snapshot, ignore=shutil.ignore_patterns("__pycache__"))
    for path in (REPO / "tests").glob("b16a_*.py"):
        shutil.copy2(path, snapshot / path.name)
    plugin = args.root / ("plugin-" + str(args.shard))
    plugin.mkdir(exist_ok=True)
    (plugin / "mutcheck.py").write_text("""import os,sys,json
from pathlib import Path
import metamuse.safety,metamuse.content,metamuse._muse_lifecycle

def pytest_sessionfinish(session,exitstatus):
    root=Path(os.environ['MUTANT_SRC']).resolve()
    modules=[metamuse.safety,metamuse.content,metamuse._muse_lifecycle]
    helper=os.environ.get('MUTANT_HELPER')
    if helper:
        modules.append(sys.modules[helper])
    paths=[str(Path(m.__file__).resolve()) for m in modules]
    assert all(Path(p).is_relative_to(root) for p in paths),paths
    Path(os.environ['MUTCHECK_OUT']).write_text(json.dumps(paths))
""")
    eqscript = plugin / "equivalence.py"
    eqscript.write_text("""import hashlib,json
from b16a_r3_corpus import differential_corpus
from metamuse.safety import redact_text
digest=hashlib.sha256();count=0
for name,text in enumerate(differential_corpus()):
    out,findings=redact_text(text)
    digest.update(json.dumps([name,out,findings,redact_text(out)],ensure_ascii=True).encode())
    count+=1
print(json.dumps({'inputs':count,'sha256':digest.hexdigest()}))
""")
    head_hash = None
    for index, name in enumerate(args.names or MUTANTS):
        if index % args.shards != args.shard:
            continue
        file, edit, tests, selection, equivalent = MUTANTS[name]
        base = args.root / name
        source = base / "src"
        if source.exists():
            shutil.rmtree(source)
        shutil.copytree(snapshot, source)
        path = source / file
        before = path.read_text()
        try:
            after = edit(before)
            assert after != before
            compile(after, str(path), "exec")
            path.write_text(after)
        except Exception as error:
            print(json.dumps({"mutant": name, "adaptation_error": str(error)}), flush=True)
            continue
        (base / "mutation.diff").write_text(
            "".join(
                difflib.unified_diff(
                    before.splitlines(True),
                    after.splitlines(True),
                    "head/" + file,
                    "mutant/" + file,
                )
            )
        )
        env = {
            **os.environ,
            "UV_OFFLINE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": f"{source}:{plugin}:{REPO / 'tests'}",
            "MUTANT_SRC": str(source),
            "MUTCHECK_OUT": str(base / "imports.json"),
        }
        if not file.startswith("metamuse/"):
            env["MUTANT_HELPER"] = Path(file).stem
        command = [
            "pytest-disktmp",
            "env",
            "UV_OFFLINE=1",
            "uv",
            "run",
            "pytest",
            "-q",
            "--import-mode=importlib",
            "-p",
            "no:cacheprovider",
            "-p",
            "mutcheck",
            "-rf",
            *(tests if isinstance(tests, list) else [tests]),
        ]
        if selection:
            command += ["-k", selection]
        try:
            result = subprocess.run(
                command, cwd=REPO, env=env, text=True, capture_output=True, timeout=600
            )
            output = result.stdout + result.stderr
            failed = [
                line.split(" ")[1]
                for line in result.stdout.splitlines()
                if line.startswith("FAILED ")
            ]
            row = {
                "mutant": name,
                "exit": result.returncode,
                "killed": result.returncode == 1
                and bool(failed)
                and (base / "imports.json").exists(),
                "failed": failed,
                "command": command,
                "summary": result.stdout.strip().splitlines()[-1]
                if result.stdout.strip()
                else result.stderr[-200:],
            }
            if result.returncode == 0 and equivalent:
                if head_hash is None:
                    r = subprocess.run(
                        ["uv", "run", "python", str(eqscript)],
                        cwd=REPO,
                        env={**env, "PYTHONPATH": f"{snapshot}:{REPO / 'tests'}"},
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    head_hash = json.loads(r.stdout)
                r = subprocess.run(
                    ["uv", "run", "python", str(eqscript)],
                    cwd=REPO,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                proof = json.loads(r.stdout)
                row["equivalent_inputs"] = proof["inputs"] if proof == head_hash else 0
                row["head_proof"] = head_hash
                row["mutant_proof"] = proof
        except subprocess.TimeoutExpired as error:
            output = (
                (error.stdout or b"").decode()
                if isinstance(error.stdout, bytes)
                else error.stdout or ""
            )
            row = {"mutant": name, "timeout": 600, "killed": False}
        (base / "pytest.log").write_text(" ".join(command) + "\n" + output)
        with (args.root / ("results-" + str(args.shard) + ".jsonl")).open("a") as stream:
            stream.write(json.dumps(row) + "\n")
        print(json.dumps(row), flush=True)


if __name__ == "__main__":
    import b16a_r6_mutations  # noqa: F401

    main()
