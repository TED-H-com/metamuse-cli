"""Try the shipped CLI on synthetic comments and keep a sourced Markdown example."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from platformdirs import user_data_path

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, help="A new directory outside the checkout")
    args = parser.parse_args()
    cli = shutil.which("metamuse", path=str(Path(sys.executable).parent))
    if not cli:
        parser.error("Run uv sync, then uv run python scripts/try_local.py")
    os.umask(0o077)
    if args.workspace:
        workspace = args.workspace.expanduser().resolve()
        if workspace.is_relative_to(REPO) or workspace.exists():
            parser.error(
                "Choose a new workspace directory outside the checkout; existing data is kept."
            )
        workspace.mkdir(parents=True, mode=0o700)
    else:
        demos = user_data_path("metamuse") / "demos"
        demos.mkdir(parents=True, exist_ok=True, mode=0o700)
        workspace = Path(tempfile.mkdtemp(prefix="comments-", dir=demos))

    def mm(*arguments: str) -> dict:
        process = subprocess.run(
            [cli, "--workspace", str(workspace), "--json", "--non-interactive", *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
        if process.returncode:
            print(process.stdout, file=sys.stderr)
            print(process.stderr, file=sys.stderr)
            raise RuntimeError(f"MetaMuse command failed ({process.returncode}): {arguments[0]}")
        return json.loads(process.stdout)["data"]

    print("Local demo: synthetic comments, a prewritten recap, no account or remote service.")
    print(f"Workspace: {workspace}", flush=True)
    mm("init")
    inputs = workspace / "inputs"
    inputs.mkdir(mode=0o700)
    source = inputs / "event-comments.jsonl"
    shutil.copyfile(REPO / "examples/event-comments.jsonl", source)
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
    print("Imported 3 synthetic records. Search for 字幕 found 1 comment.")
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
    print("Saved a prewritten recap with its source reference and exported Markdown.")
    print(f"Project: {project_id}")
    print(f"Export directory: {output}")
    print("Open a .md file in that directory to read the recap and cited evidence.")
    print("Your demo stays on this machine. To remove it, delete the workspace shown above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
