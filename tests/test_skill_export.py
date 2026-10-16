"""Packaged Skill matches the canonical tree and exports only where asked."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from metamuse import mcp
from metamuse.cli import main
from metamuse.skill_export import packaged_skill_files

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "skills" / "metamuse"


def _canonical_files() -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    for path in CANONICAL.rglob("*"):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        found[path.relative_to(CANONICAL).as_posix()] = path.read_bytes()
    assert "SKILL.md" in found
    return found


def test_packaged_copy_matches_canonical_tree() -> None:
    packaged = dict(packaged_skill_files())
    assert packaged == _canonical_files()


def test_skill_export_writes_canonical_bytes_and_refuses_overwrite(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    dest = tmp_path / "skill"
    env = {**os.environ, "HOME": str(home), "PYTHONUNBUFFERED": "1"}
    completed = subprocess.run(
        [sys.executable, "-m", "metamuse.cli", "skill", "export", "--dest", str(dest)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    canonical = _canonical_files()
    exported = {
        path.relative_to(dest).as_posix(): path.read_bytes()
        for path in dest.rglob("*")
        if path.is_file()
    }
    assert exported == canonical
    (dest / "SKILL.md").write_text("changed\n", encoding="utf-8")
    refused = main(["--json", "skill", "export", "--dest", str(dest)])
    assert refused == 2
    assert (dest / "SKILL.md").read_text(encoding="utf-8") == "changed\n"
    replaced = subprocess.run(
        [
            sys.executable,
            "-m",
            "metamuse.cli",
            "--json",
            "skill",
            "export",
            "--dest",
            str(dest),
            "--force",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=env,
    )
    assert replaced.returncode == 0, replaced.stderr
    envelope = json.loads(replaced.stdout)
    assert envelope["status"] == "ok"
    assert envelope["data"]["file_count"] == len(canonical)
    assert (dest / "SKILL.md").read_bytes() == canonical["SKILL.md"]
    assert not (home / ".claude").exists()
    assert not (home / ".codex").exists()
    assert not (home / ".config").exists()
    names = {tool.name for tool in mcp.list_tools()}
    assert len(names) == 19
    assert "metamuse_skill_export" not in names


def test_wheel_skill_matches_canonical_source(tmp_path: Path) -> None:
    out = tmp_path / "dist"
    env = {**os.environ, "UV_OFFLINE": "1"}
    completed = subprocess.run(
        ["uv", "build", "--wheel", "--offline", "--out-dir", str(out)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    wheels = list(out.glob("*.whl"))
    assert len(wheels) == 1
    prefix = "metamuse/data/skills/metamuse/"
    with zipfile.ZipFile(wheels[0]) as archive:
        names = archive.namelist()
        packaged = {
            name[len(prefix) :]: archive.read(name)
            for name in names
            if name.startswith(prefix) and not name.endswith("/")
        }
        assert "metamuse/data/THIRD_PARTY_NOTICES.md" in names
        assert any(
            name.startswith("metamuse/data/migrations/") and name.endswith(".sql") for name in names
        )
        assert any(
            name.startswith("metamuse/data/schemas/") and name.endswith(".json") for name in names
        )
    assert packaged == _canonical_files()
