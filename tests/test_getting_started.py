"""Check the first-use story and preservation of a user's existing workspace."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("entrypoint", ["script", "cli"])
def test_try_local_exports_real_sourced_markdown(tmp_path, entrypoint):
    workspace = tmp_path / "first-use"
    command = (
        [sys.executable, "scripts/try_local.py", "--workspace", str(workspace)]
        if entrypoint == "script"
        else [sys.executable, "-m", "metamuse.cli", "--workspace", str(workspace), "demo"]
    )
    result = subprocess.run(
        command,
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=90,
        env={**os.environ, "UV_OFFLINE": "1"},
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "Search for 字幕 found 1 comment" in result.stdout
    assert 'network_mode = "offline"' in (workspace / "metamuse.toml").read_text()
    manifests = list(workspace.rglob("manifest.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text())
    markdown = list(manifests[0].parent.glob("*.md"))
    assert markdown
    text = "\n".join(p.read_text() for p in markdown)
    assert "預寫示例，非 AI 分析" in text
    assert "https://example.org/events/demo/comments/1" in text
    assert manifest["selection"][0]["kind"] == "draft_revision"
    assert "item_revision" in text
    for member in manifest["files"]:
        payload = (manifests[0].parent / member["name"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == member["sha256"]


@pytest.mark.parametrize("entrypoint", ["script", "cli"])
def test_try_local_keeps_existing_directory_untouched(tmp_path, entrypoint):
    marker = tmp_path / "keep.txt"
    marker.write_text("User data stays here.")
    command = (
        [sys.executable, "scripts/try_local.py", "--workspace", str(tmp_path)]
        if entrypoint == "script"
        else [sys.executable, "-m", "metamuse.cli", "--workspace", str(tmp_path), "demo"]
    )
    result = subprocess.run(
        command,
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "existing data is kept" in result.stderr
    assert marker.read_text() == "User data stays here."
    assert sorted(p.name for p in tmp_path.iterdir()) == ["keep.txt"]


def test_demo_json_is_one_envelope_and_uses_default_private_location(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "metamuse.cli", "--json", "demo"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=90,
        env={**os.environ, "UV_OFFLINE": "1", "XDG_DATA_HOME": str(tmp_path / "data")},
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    data = json.loads(result.stdout)["data"]
    assert data["synthetic_data"] is True and data["prewritten_recap"] is True
    workspace = Path(data["workspace"])
    assert workspace.is_relative_to(tmp_path / "data" / "metamuse" / "demos")
    assert workspace.stat().st_mode & 0o077 == 0
    assert list(Path(data["export_directory"]).glob("*.md"))
