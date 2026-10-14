"""Static SQL restrictions prevent application bugs from bypassing storage guards."""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# Exceptions require an exact (relative path, source line) and a reviewed explanation.
# There are currently no authorized conflict clauses or rowid writes.
SQL_ALLOWLIST: dict[tuple[str, int], str] = {}


def violations(sql):
    sql = re.sub(r"/\*.*?\*/|--[^\n]*", "", sql, flags=re.S)
    bad = []
    if re.search(
        r"\b(?:INSERT|UPDATE)\s+OR\s+\w+|\bOR\s+(?:REPLACE|IGNORE)\b|\bREPLACE\s+INTO\b|\bON\s+CONFLICT\b",
        sql,
        re.I,
    ):
        bad.append("conflict clause")
    for statement in sql.split(";"):
        write = re.search(
            r"\b(?:INSERT\s+INTO|UPDATE\s+[\w\"]+\s+SET|DELETE\s+FROM)\b", statement, re.I
        )
        if write and re.search(r"\b(?:rowid|_rowid_|oid)\b", statement[write.start() :], re.I):
            bad.append("rowid write")
    return bad


def string_value(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value if isinstance(part, ast.Constant) else "{expression}" for part in node.values
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return (string_value(node.left) or "") + (string_value(node.right) or "")
    return None


def test_r2_f1_application_sql_has_no_conflict_clauses_or_rowid_writes():
    found = []
    for path in sorted((ROOT / "src/metamuse").rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            value = string_value(node)
            if value is None:
                continue
            location = (str(path.relative_to(ROOT)), node.lineno)
            if violations(value) and location not in SQL_ALLOWLIST:
                found.append(location)
    for path in sorted((ROOT / "src/metamuse/data/migrations").glob("*.sql")):
        location = (str(path.relative_to(ROOT)), 1)
        if violations(path.read_text()) and location not in SQL_ALLOWLIST:
            found.append(location)
    assert not found, f"Forbidden SQL at {sorted(set(found))}"


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT OR REPLACE INTO t VALUES(1)",
        "REPLACE INTO t VALUES(1)",
        "INSERT OR IGNORE INTO t VALUES(1)",
        "INSERT OR ABORT INTO t VALUES(1)",
        "UPDATE OR FAIL t SET id=1",
        "INSERT INTO t VALUES(1) ON CONFLICT DO NOTHING",
        "INSERT INTO t (_rowid_,id) VALUES(1,2)",
        "UPDATE t SET oid=2",
        "DELETE FROM t WHERE rowid=1",
    ],
)
def test_r2_f1_sql_policy_detects_every_forbidden_form(sql):
    assert violations(sql)


def test_r2_f5_read_only_rowid_ordering_is_allowed():
    assert not violations("SELECT rowid FROM t ORDER BY rowid")
