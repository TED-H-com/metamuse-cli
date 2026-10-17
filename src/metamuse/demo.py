"""Try the shipped CLI on synthetic comments and keep a sourced Markdown example."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from importlib.resources import files
from pathlib import Path

from platformdirs import user_data_path

from metamuse import models as m

_CODE_ROOT = Path(__file__).resolve().parents[2]


def run_demo(workspace: Path | None = None) -> dict:
    """Use only the shipped CLI and synthetic input in a new private workspace."""
    previous_umask = os.umask(0o077)
    try:
        return _run_demo(workspace)
    finally:
        os.umask(previous_umask)


def _run_demo(workspace: Path | None) -> dict:
    if workspace is not None:
        workspace = workspace.expanduser().resolve()
        if workspace.is_relative_to(_CODE_ROOT) or workspace.exists():
            raise m.CoreError(
                m.ErrorCode.INPUT_INVALID,
                "Choose a new workspace directory outside the code directory; "
                "existing data is kept.",
            )
        workspace.mkdir(parents=True, mode=0o700)
    else:
        demos = user_data_path("metamuse") / "demos"
        demos.mkdir(parents=True, exist_ok=True, mode=0o700)
        workspace = Path(tempfile.mkdtemp(prefix="comments-", dir=demos))

    def mm(*arguments: str) -> dict:
        process = subprocess.run(
            [
                sys.executable,
                "-I",
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
        if process.returncode:
            raise m.CoreError(
                m.ErrorCode.STORAGE,
                f"Local demo stopped during {arguments[0]}",
                next_action=f"Inspect the new demo workspace: {workspace}",
            )
        return json.loads(process.stdout)["data"]

    mm("init")
    inputs = workspace / "inputs"
    inputs.mkdir(mode=0o700)
    source = inputs / "event-comments.jsonl"
    source.write_bytes(
        files("metamuse").joinpath("data/examples/event-comments.jsonl").read_bytes()
    )
    config = workspace / "metamuse.toml"
    config.write_text(
        config.read_text().replace(
            "[app]\n", "[app]\nimport_roots = " + json.dumps([str(inputs)]) + "\n", 1
        )
    )
    project = mm(
        "project", "create", "--name", "Sample event recap", "--request-key", "demo-project"
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
    counts = imported["job"]["observed_scope"]["steps"][0]
    if imported["state"] != "succeeded" or counts["committed"] != 3 or counts["rejected"]:
        raise RuntimeError("The synthetic import did not finish with all 3 records; demo stopped.")
    hits = mm("search", "--project", project_id, "--query", "字幕")["results"]
    if len(hits) != 1:
        raise RuntimeError("The synthetic subtitle request was not found; demo stopped.")
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
    mm("packet", "preview", packet["id"])
    draft_text = inputs / "recap.txt"
    draft_text.write_text(
        "活動回顧範例（預寫示例，非 AI 分析）\n\n"
        "一則合成留言希望影片提供字幕，方便回看。\n"
        "引用：https://example.org/events/demo/comments/1\n"
    )
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
    mm("draft", "preview", draft["draft_id"], "--revision", "1")
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
    output = workspace / exported["artifact"]
    return {
        "demo": True,
        "synthetic_data": True,
        "prewritten_recap": True,
        "network_mode": "offline",
        "workspace": str(workspace),
        "project_id": project_id,
        "export_directory": str(output),
        "imported_records": 3,
        "search_hits": len(hits),
    }


def describe(data: dict) -> str:
    return "\n".join(
        [
            "Local demo: synthetic comments, a prewritten recap, no account or remote service.",
            f"Workspace: {data['workspace']}",
            "Imported 3 synthetic records. Search for 字幕 found 1 comment.",
            "Saved a prewritten recap with its source reference and exported Markdown.",
            f"Project: {data['project_id']}",
            f"Export directory: {data['export_directory']}",
            "Open a .md file in that directory to read the recap and cited evidence.",
            "Your demo stays on this machine. To remove it, delete the workspace shown above.",
        ]
    )
