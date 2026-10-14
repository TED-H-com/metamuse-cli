"""Execute the README's literal Bash walkthrough through shipped commands."""

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from test_journey import verify_bundle_or_markdown


def test_readme_offline_walkthrough_commands_as_written(tmp_path):
    checkout = Path(__file__).resolve().parents[1]
    section = (
        (checkout / "README.md")
        .read_text()
        .split("## Local offline walkthrough\n", 1)[1]
        .split("\n## Skill", 1)[0]
    )
    blocks = re.findall(r"```bash\n(.*?)\n```", section, re.S)
    assert len(blocks) == 3
    script = "\n".join(blocks)
    assert "from metamuse" not in script and "import metamuse" not in script
    assert "permit create" not in script
    root = tmp_path / "walkthrough"
    root.mkdir()
    result = subprocess.run(
        ["bash", "-e", "-u", "-o", "pipefail"],
        input=script,
        text=True,
        cwd=checkout,
        env={**os.environ, "UV_OFFLINE": "1", "SLICE_DIR": str(root), "SLICE_KEEP": "1"},
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "Expires at (UTC):" in result.stderr and "Max uses: 1" in result.stderr
    assert "live_verified" not in result.stdout + result.stderr

    def data(name):
        return json.loads((root / (name + ".json")).read_text())["data"]

    discussion = data("open")["discussion_id"]
    observations = []
    for number in (1, 2):
        assert data(f"send-{number}")["discussion_id"] == discussion
        read = data(f"read-{number}")
        assert read["mock"] and read["backend"] == "fixture" and read["research_complete"] is False
        assistant_ids = {
            message["id"] for message in read["messages"] if message["role"] == "assistant"
        }
        observed = next(
            o
            for o in read["observations"]
            if o["message_id"] in assistant_ids
            and o["raw_event"].get("payload", {}).get("role") == "assistant"
        )
        observations.append(observed)
        for operation in ("send", "read"):
            envelope = json.loads((root / f"{operation}-preview-{number}.json").read_text())
            assert envelope["error"]["code"] == "E_SCOPE_REQUIRED" and envelope["approval"]
        assert data(f"close-{number}")["outcome"] == "needs_more_data"
    assert len({o["id"] for o in observations}) == 2
    assert {e["ref"]["id"] for e in data("preview")["muse_evidence"]} == {
        o["id"] for o in observations
    }
    text = data("preview")["text"]
    assert all(o["id"] in text for o in observations)
    for format_name in ("bundle", "jsonl", "markdown", "text", "csv"):
        exported = data("export-" + format_name)
        directory = root / "workspace" / exported["artifact"]
        manifest = json.loads((directory / "manifest.json").read_text())
        assert manifest == exported["manifest"]
        assert manifest["backend"] == "fixture" and manifest["mock"] is True
        assert manifest["research_complete"] is False and manifest["redaction_notice"]
        assert {e["ref"]["id"] for e in manifest["muse_evidence"]} == {
            o["id"] for o in observations
        }
        for member in manifest["files"]:
            raw = (directory / member["name"]).read_bytes()
            assert (
                len(raw) == member["bytes"] and hashlib.sha256(raw).hexdigest() == member["sha256"]
            )
            assert b"live_verified" not in raw
        if format_name in ("bundle", "markdown"):
            verify_bundle_or_markdown(root / "workspace", exported, observations, text)
    # Optional report evidence retains command output outside the public repository.
    evidence = os.environ.get("JOURNEY_ROUND2_TRANSCRIPT")
    if evidence:
        Path(evidence).write_text(result.stdout + "\nSTDERR:\n" + result.stderr)
