"""End-to-end tests for social evidence harvesting and draft formatting."""

import json
import subprocess
import sys
from pathlib import Path


def test_e2e_social_evidence_harvesting_and_drafting(tmp_path):
    workspace = tmp_path / "workspace"
    inputs = tmp_path / "inputs"
    inputs.mkdir(parents=True)

    def mm(*arguments: str) -> dict:
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "metamuse.cli",
                "--workspace",
                str(workspace),
                "--json",
                "--non-interactive",
                *arguments,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert process.returncode == 0, (
            f"Command failed: {arguments}\nError: {process.stderr}\nOutput: {process.stdout}"
        )
        return json.loads(process.stdout)["data"]

    mm("init")

    # 1. CLI post ingestion
    corpus = Path("tests/fixtures/corpus/import/posts_zh.jsonl")
    source = inputs / "posts.jsonl"
    source.write_bytes(corpus.read_bytes())

    config = workspace / "metamuse.toml"
    config.write_text(
        config.read_text().replace(
            "[app]\n", "[app]\nimport_roots = " + json.dumps([str(inputs)]) + "\n", 1
        )
    )

    project = mm(
        "project", "create", "--name", "Social Evidence Test", "--request-key", "demo-project"
    )
    project_id = project["id"]

    imported = mm(
        "add",
        "--project",
        project_id,
        "--file",
        str(source),
        "--kind",
        "jsonl",
        "--request-key",
        "demo-import",
        "--run",
    )
    assert imported["state"] == "succeeded"

    # 2. Evidence tag extraction (search)
    hits = mm("search", "--project", project_id, "--query", "社群治理實務指南")["results"]
    assert len(hits) > 0

    # Check that evidence tag was extracted properly
    assert "ref" in hits[0]
    assert "kind" in hits[0]["ref"]
    assert "id" in hits[0]["ref"]

    selection = inputs / "selection.json"
    selection.write_text(json.dumps([hit["ref"] for hit in hits]))

    packet = mm(
        "packet",
        "prepare",
        "--project",
        project_id,
        "--selection-file",
        str(selection),
        "--request-key",
        "demo-packet",
    )
    assert packet["id"]

    # 3. Drafting template generation / draft prompt formatting
    draft_text = inputs / "draft.txt"
    draft_text.write_text("Draft template with social evidence\n")
    draft = mm(
        "draft",
        "save",
        "--project",
        project_id,
        "--text-file",
        str(draft_text),
        "--platform",
        "generic",
        "--facts-file",
        str(selection),
        "--request-key",
        "demo-draft",
    )
    assert draft["draft_id"]

    preview = mm("draft", "preview", draft["draft_id"], "--revision", "1")
    assert "digest" in preview
    assert "text" in preview
    assert preview["text"] == "Draft template with social evidence\n"

    refs = inputs / "export-refs.json"
    refs.write_text(
        json.dumps([{"kind": "draft_revision", "id": draft["draft_id"], "revision": 1}])
    )
    exported = mm(
        "export",
        "--project",
        project_id,
        "--refs-file",
        str(refs),
        "--format",
        "markdown",
        "--request-key",
        "demo-export",
    )
    assert "artifact" in exported
