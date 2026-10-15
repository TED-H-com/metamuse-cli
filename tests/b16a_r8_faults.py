"""Single-site curl/SQL faults with exact adaptation and import canaries."""

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

TEST = "tests/test_b16a_round8_boundaries.py"
FAULTS = (
    (
        "ignore_escape",
        'if char == "\\\\" and pos + 1 < len(text)',
        'if False and char == "\\\\" and pos + 1 < len(text)',
        "confirmed_curl_scope",
    ),
    (
        "unowned_argument_quote",
        'elif char in "\'\\"`“‘" and (self.argument or self.option):',
        'elif False and char in "\'\\"`“‘" and (self.argument or self.option):',
        "confirmed_curl_scope",
    ),
    (
        "ignore_real_separator",
        "and level <= self.level",
        "and level < self.level",
        "even_backslashes_leave_real_separator",
    ),
    (
        "skip_exempt_argument",
        "and pos >= self.protected_end",
        "and True",
        "exempt_lookup_argument_separator",
    ),
    (
        "principal_is_retroactive",
        "not sql_range[0] <= match.start() < sql_range[1]",
        "not 0 <= match.start() < sql_range[1]",
        "later_sql_principal",
    ),
    (
        "lose_source_parity",
        "_merge_spans([spans, _curl_source_spans(text)])",
        "_merge_spans([spans, ()])",
        "local_argument_quotes or confirmed_curl_scope",
    ),
    (
        "expose_selected_tail",
        "yield max(begin, boundary + 1), finish, kind, _REDACTED",
        "pass",
        "scope_reset_never_exposes_selected_credential_tail",
    ),
)


def run(root):
    source = Path("src/metamuse/safety.py").read_text()
    for name, old, new, selection in FAULTS:
        assert old != new and source.count(old) == 1, (name, "adaptation is not single-site")
        mutated = source.replace(old, new, 1)
        ast.parse(mutated)
        assert mutated != source
        target = root / name / "src" / "metamuse"
        shutil.copytree("src/metamuse", target, ignore=shutil.ignore_patterns("__pycache__"))
        path = target / "safety.py"
        path.write_text(mutated)
        expected = hashlib.sha256(path.read_bytes()).hexdigest()
        env = dict(
            os.environ,
            UV_OFFLINE="1",
            PYTHONPATH=str(target.parent.resolve()) + os.pathsep + str(Path("tests").resolve()),
        )
        canary = subprocess.run(
            [
                sys.executable,
                "-c",
                "import hashlib, pathlib, metamuse.safety as s; "
                "print(hashlib.sha256(pathlib.Path(s.__file__).read_bytes()).hexdigest())",
            ],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        assert canary.stdout.strip() == expected, (name, "wrong source imported")
        result = subprocess.run(
            [
                "pytest-disktmp",
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--import-mode=importlib",
                TEST,
                "-k",
                selection,
            ],
            env=env,
            capture_output=True,
            text=True,
        )
        (root / (name + ".log")).write_text(result.stdout + result.stderr)
        killed = (
            result.returncode == 1
            and "AssertionError" in result.stdout
            and " failed" in result.stdout
            and "ERROR " not in result.stdout
        )
        print(
            json.dumps(
                {
                    "fault": name,
                    "selection": selection,
                    "import_sha": expected,
                    "applied_sites": 1,
                    "killed": killed,
                    "exit": result.returncode,
                    "summary": result.stdout.strip().splitlines()[-1],
                }
            ),
            flush=True,
        )
        if not killed:
            raise AssertionError(name)


if __name__ == "__main__":
    run(Path(sys.argv[1]))
