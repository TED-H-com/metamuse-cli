"""Offline stdlib slices, with every refused line retained as evidence."""

import ast
import io
import json
import sysconfig
import tokenize
from pathlib import Path

from metamuse.safety import redact_text


def slices():
    stdlib = Path(sysconfig.get_path("stdlib"))
    tops = sorted(stdlib.glob("*.py"))
    files = tops + sorted((stdlib / "json").glob("*.py")) + sorted((stdlib / "email").glob("*.py"))

    def lines(paths, equal=False):
        return [
            s
            for p in paths
            for line in p.read_text(errors="ignore").splitlines()
            if (s := line.strip())
            and len(s) < 300
            and (not equal or ("=" in s and not s.startswith("#")))
        ]

    yield "established", lines(files, True)
    yield "all_lines", lines(tops)
    yield (
        "recursive_email",
        lines(
            tops
            + sorted((stdlib / "json").glob("*.py"))
            + sorted((stdlib / "email").rglob("*.py")),
            True,
        ),
    )
    docs = []
    comments = []
    for p in tops:
        src = p.read_text(errors="ignore")
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                docs.extend(
                    s
                    for line in (ast.get_docstring(node) or "").splitlines()
                    if (s := line.strip()) and len(s) < 300
                )
        comments.extend(
            s
            for t in tokenize.generate_tokens(io.StringIO(src).readline)
            if t.type == tokenize.COMMENT and (s := t.string.lstrip("#").strip()) and len(s) < 300
        )
    yield "docstrings", docs
    yield "comments", comments


def measure():
    for name, values in slices():
        changed = 0
        refused = []
        for line in values:
            out, found = redact_text(line)
            changed += (out, found) != (line, [])
            if any(row["kind"] != "local_path" for row in found):
                refused.append({"line": line, "out": out, "findings": found})
        yield {
            "slice": name,
            "total": len(values),
            "changed": changed,
            "refused": len(refused),
            "percent": 100 * len(refused) / len(values),
            "examples": refused,
        }


if __name__ == "__main__":
    for row in measure():
        print(json.dumps(row), flush=True)
