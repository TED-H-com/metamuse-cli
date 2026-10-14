#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow==12.1.1"]
# ///
"""Capture real Alacritty windows of the installed MetaMuse CLI.

Each scene is a shell that prints a command and then runs it. Pillow
ImageGrab grabs the Xvfb display. The crop is the terminal window only.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageGrab

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from site_lib import privacy_failures  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DEMO = Path("/tmp/metamuse-demo")
WS = DEMO / "workspace"
OUT = REPO / "site" / "images"
COLUMNS = 112
LINES = 68
SCREEN = "1500x1800x24"
DISPLAY = ":61"

# (filename stem, what the reader sees, commands)
SCENES: list[tuple[str, str, list[str]]] = [
    (
        "01-health",
        "Offline init, fixture Muse config, and doctor. "
        "network_mode=offline, backend = fixture, verification fixture_passed, "
        "worker_available=false. Muse open and send stay effective=false.",
        [
            "metamuse --workspace /tmp/metamuse-demo/workspace init",
            "python3 /tmp/metamuse-demo/configure_demo_workspace.py",
            (
                "sed -n '1,4p;/^\\[muse\\]/,/^\\[publishing\\]/p' "
                "/tmp/metamuse-demo/workspace/metamuse.toml"
            ),
            (
                "metamuse --workspace /tmp/metamuse-demo/workspace doctor | jq -r "
                "'\"network_mode=\\(.network_mode) "
                'worker_available=\\(.worker_available)", '
                '(.capabilities[] | "\\(.name) \\(.verification) \\(.environment) '
                "\\(.permission) effective=\\(.effective)\")'"
            ),
        ],
    ),
    (
        "02-import",
        "Create a project and import the synthetic mixed file with --run. "
        "The job ends partial, stop row_errors, 7 committed and 7 rejected. "
        "worker_available stays false after the foreground run returns.",
        [
            (
                "metamuse --workspace /tmp/metamuse-demo/workspace project create "
                "--name 'Synthetic demo' --request-key project | "
                "tee /tmp/metamuse-demo/out/project.json | jq '{id, name, revision}'"
            ),
            (
                "jq -r .id /tmp/metamuse-demo/out/project.json | "
                "tee /tmp/metamuse-demo/state/project_id"
            ),
            (
                "metamuse --workspace /tmp/metamuse-demo/workspace add --project "
                "$(cat /tmp/metamuse-demo/state/project_id) --file "
                "/tmp/metamuse-demo/inputs/comments_mixed_bad.jsonl --kind jsonl "
                "--request-key import --run | tee /tmp/metamuse-demo/out/add.json | "
                "jq '{job_id, state, worker_available, coverage: .job.coverage_status, "
                "stop: .job.stop_reason, committed: .job.observed_scope.steps[0].committed, "
                "rejected: .job.observed_scope.steps[0].rejected}'"
            ),
            "jq -r .job_id /tmp/metamuse-demo/out/add.json | tee /tmp/metamuse-demo/state/job_id",
        ],
    ),
    (
        "03-rows",
        "jobs get for that import. Each line is committed or rejected. "
        "Rejected lines keep their error code. Coverage stays partial.",
        [
            (
                "metamuse --workspace /tmp/metamuse-demo/workspace jobs get "
                "$(cat /tmp/metamuse-demo/state/job_id) | jq -r "
                "'\"state=\\(.job.state) coverage=\\(.job.coverage_status) "
                'stop=\\(.job.stop_reason)", '
                '"committed=\\(.steps[0].result.observed_scope.committed) '
                'rejected=\\(.steps[0].result.observed_scope.rejected)", '
                "(.steps[0].result.data.rows[] | "
                '"line=\\(.line) \\(.status) code=\\(.code // "-") '
                'field=\\(.field // "-")")\''
            )
        ],
    ),
    (
        "04-search",
        "Exact-substring search. The hit cites one item revision, with "
        "collection_method file_import and upstream synthetic_fixture.",
        [
            (
                "metamuse --workspace /tmp/metamuse-demo/workspace search --project "
                "$(cat /tmp/metamuse-demo/state/project_id) --query '正常留言第一行' --limit 1 | "
                "tee /tmp/metamuse-demo/out/search.json | jq -r "
                '\'"search_mode=\\(.search_mode)", '
                '"ref=\\(.results[0].ref.kind) \\(.results[0].ref.id) '
                'revision=\\(.results[0].ref.revision)", '
                '"stale=\\(.results[0].stale)", '
                '"platform=\\(.results[0].source.platform)", '
                '"collection_method=\\(.results[0].source.collection_method)", '
                '"upstream=\\(.results[0].source.upstream_collection_method)", '
                '"complete=\\(.results[0].source.complete)", '
                '"snippet=\\(.results[0].snippet)"\''
            )
        ],
    ),
    (
        "05-packet",
        "Import the synthetic secrets file, select one revision, and preview the packet. "
        "The frame shows the exact pre-share text, redaction counts, and the SHA-256 digest. "
        "The token value is not printed; the preview shows [REDACTED].",
        [
            (
                "metamuse --workspace /tmp/metamuse-demo/workspace add --project "
                "$(cat /tmp/metamuse-demo/state/project_id) --file "
                "/tmp/metamuse-demo/inputs/secrets.jsonl --kind jsonl "
                "--request-key secrets --run | tee /tmp/metamuse-demo/out/secrets-add.json | "
                "jq '{state, stop: .job.stop_reason, "
                "committed: .job.observed_scope.steps[0].committed, "
                "rejected: .job.observed_scope.steps[0].rejected}'"
            ),
            (
                "metamuse --workspace /tmp/metamuse-demo/workspace search --project "
                "$(cat /tmp/metamuse-demo/state/project_id) --query '存取網址' --limit 1 | "
                "tee /tmp/metamuse-demo/out/secret-search.json | jq "
                "'{search_mode, ref: .results[0].ref, "
                "upstream: .results[0].source.upstream_collection_method, "
                "collection: .results[0].source.collection_method, "
                "complete: .results[0].source.complete}'"
            ),
            (
                "jq '[.results[0].ref]' /tmp/metamuse-demo/out/secret-search.json | "
                "tee /tmp/metamuse-demo/inputs/selection.json"
            ),
            (
                "metamuse --workspace /tmp/metamuse-demo/workspace packet prepare --project "
                "$(cat /tmp/metamuse-demo/state/project_id) --selection-file "
                "/tmp/metamuse-demo/inputs/selection.json --request-key packet | "
                "tee /tmp/metamuse-demo/out/packet.json | jq -r "
                '\'"id=\\(.id)", "digest=\\(.digest)", '
                '"coverage=\\(.coverage_status)", '
                '"stale=\\(.stale)", "utf8_bytes=\\(.utf8_bytes)", '
                '"redactions=\\(.redactions)", '
                '"--- exact text ---", .rendered_text\''
            ),
        ],
    ),
    (
        "06-draft",
        "The host writes the draft text. draft save stores that text and cites "
        "the local item revision from search. No model is called.",
        [
            (
                'printf "%s\\n" "活動紀錄（host 撰寫）" '
                '"本地留言仍在。壞列沒有被算成完整匯入。" | '
                "tee /tmp/metamuse-demo/inputs/draft.txt"
            ),
            (
                "jq '[.results[0].ref]' /tmp/metamuse-demo/out/search.json | "
                "tee /tmp/metamuse-demo/inputs/facts.json"
            ),
            (
                "metamuse --workspace /tmp/metamuse-demo/workspace draft save --project "
                "$(cat /tmp/metamuse-demo/state/project_id) --text-file "
                "/tmp/metamuse-demo/inputs/draft.txt --platform generic --facts-file "
                "/tmp/metamuse-demo/inputs/facts.json --request-key draft | "
                "tee /tmp/metamuse-demo/out/draft.json | jq -r "
                '\'"draft_id=\\(.draft_id)", "revision=\\(.revision)", '
                '"digest=\\(.digest)", '
                '"stale=\\(.stale)", "platform=\\(.platform)", '
                '"facts=\\(.facts_refs)", '
                '"--- host text ---", .text\''
            ),
            (
                "jq -r .draft_id /tmp/metamuse-demo/out/draft.json | "
                "tee /tmp/metamuse-demo/state/draft_id"
            ),
        ],
    ),
    (
        "07-export",
        "Bundle export of the host draft. Stderr says nothing was published or uploaded. "
        "The manifest lists text_policy, the redaction notice, and each file SHA-256.",
        [
            (
                'jq -n --arg id "$(cat /tmp/metamuse-demo/state/draft_id)" '
                "'[{kind:\"draft_revision\", id:\\$id, revision:1}]' | "
                "tee /tmp/metamuse-demo/inputs/export-refs.json"
            ),
            (
                "metamuse --workspace /tmp/metamuse-demo/workspace export --project "
                "$(cat /tmp/metamuse-demo/state/project_id) --refs-file "
                "/tmp/metamuse-demo/inputs/export-refs.json --format bundle "
                "--request-key export-bundle | tee /tmp/metamuse-demo/out/export.json | "
                "jq -r "
                '\'"id=\\(.id)", "state=\\(.state)", "artifact=\\(.artifact)", '
                '"format=\\(.manifest.format)", '
                '"text_policy=\\(.manifest.text_policy)", '
                '"asset_policy=\\(.manifest.asset_policy)", '
                '"redaction_notice=\\(.manifest.redaction_notice)", '
                '"redactions=\\(.manifest.redactions)", '
                '(.manifest.files[] | "\\(.name) sha256=\\(.sha256) '
                'bytes=\\(.bytes)"), '
                '"selection=\\(.manifest.selection)"\''
            ),
        ],
    ),
    (
        "08-mcp",
        "Official MCP Python client over metamuse mcp. It lists the tools, "
        "then calls the read-only metamuse_search tool.",
        ["python3 /tmp/metamuse-demo/mcp_demo.py"],
    ),
]


def _run(cmd: list[str], *, env: dict[str, str] | None = None, check: bool = True) -> str:
    result = subprocess.run(cmd, check=check, env=env, text=True, capture_output=True)
    return (result.stdout or "") + (result.stderr or "")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _visual_lines(text: str, columns: int) -> int:
    count = 0
    for line in text.splitlines():
        width = 0
        for char in line:
            width += 2 if ord(char) > 0x2E80 else 1
        count += max(1, (width + columns - 1) // columns)
    return count


def _sh_command(cmd: str) -> str:
    """A run invocation. Unquoted heredocs expand $(...); quoted ones do not."""
    opener = "EOF" if "$(" in cmd else "'EOF'"
    return f'run "$(cat <<{opener}\n{cmd}\nEOF\n)"\n'


def _scene_script(commands: list[str], transcript: Path, done: Path) -> str:
    body = "".join(_sh_command(cmd) + 'printf "\\n"\n' for cmd in commands)
    return f"""#!/bin/bash
set -u
set -o pipefail
export HOME=/tmp/metamuse-demo/home
export TMPDIR=/tmp/metamuse-demo/tmp
export PATH=/tmp/metamuse-demo/bin:/usr/bin:/bin
export LANG=zh_TW.UTF-8
export LC_ALL=zh_TW.UTF-8
export MM_WORKSPACE=/tmp/metamuse-demo/workspace
export MM_BIN=/tmp/metamuse-demo/bin/metamuse
export MM_PROJECT_FILE=/tmp/metamuse-demo/state/project_id
cd /tmp/metamuse-demo
exec > >(tee {transcript}) 2>&1
run() {{
  printf '$ %s\\n' "$1"
  eval "$1"
  code=$?
  if [ "$code" -ne 0 ] && [ "$code" -ne 5 ]; then
    printf 'command exited %s\\n' "$code"
    exit "$code"
  fi
}}
{body}touch {done}
sleep 90
"""


def _prepare_demo() -> None:
    if DEMO.exists():
        shutil.rmtree(DEMO)
    for name in ("home", "tmp", "inputs", "out", "state", "bin"):
        (DEMO / name).mkdir(parents=True)
    venv_bin = REPO / ".venv" / "bin"
    # The venv interpreter is itself a symlink. A second symlink resolves to the
    # base interpreter and drops the virtualenv. A wrapper keeps `python3` on
    # the demo PATH without printing the real path.
    python = DEMO / "bin" / "python3"
    python.write_text(f'#!/bin/sh\nexec {venv_bin / "python3"} "$@"\n', encoding="utf-8")
    python.chmod(0o755)
    (DEMO / "bin" / "metamuse").symlink_to(venv_bin / "metamuse")
    (DEMO / "bin" / "jq").symlink_to("/usr/bin/jq")
    corpus = REPO / "tests" / "fixtures" / "corpus"
    shutil.copy(corpus / "import" / "comments_mixed_bad.jsonl", DEMO / "inputs")
    shutil.copy(corpus / "security" / "secrets.jsonl", DEMO / "inputs")
    shutil.copy(
        REPO / "scripts" / "configure_demo_workspace.py", DEMO / "configure_demo_workspace.py"
    )
    shutil.copy(REPO / "scripts" / "mcp_demo.py", DEMO / "mcp_demo.py")
    (DEMO / "fonts.conf").write_text(
        """<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>
  <alias>
    <family>DejaVu Sans Mono</family>
    <prefer>
      <family>DejaVu Sans Mono</family>
      <family>Noto Sans CJK TC</family>
    </prefer>
  </alias>
</fontconfig>
""",
        encoding="utf-8",
    )
    (DEMO / "alacritty.toml").write_text(
        f"""[general]
live_config_reload = false
working_directory = "/tmp/metamuse-demo"

[window]
decorations = "None"
dynamic_title = false
title = "metamuse-scene"
startup_mode = "Windowed"
dimensions = {{ columns = {COLUMNS}, lines = {LINES} }}
padding = {{ x = 12, y = 10 }}
opacity = 1.0

[font]
size = 13.0

[font.normal]
family = "DejaVu Sans Mono"
style = "Regular"

[font.bold]
family = "DejaVu Sans Mono"
style = "Bold"

[colors.primary]
background = "#14211c"
foreground = "#e7f2ea"

[colors.normal]
black = "#1b2a24"
red = "#e07a5f"
green = "#8fbc8f"
yellow = "#e6c07b"
blue = "#7aa2c4"
magenta = "#c4a3c4"
cyan = "#7ec8c3"
white = "#d7e4dc"
""",
        encoding="utf-8",
    )


def _scene_env() -> dict[str, str]:
    return {
        "HOME": str(DEMO / "home"),
        "TMPDIR": str(DEMO / "tmp"),
        "PATH": f"{DEMO / 'bin'}:/usr/bin:/bin",
        "LANG": "zh_TW.UTF-8",
        "LC_ALL": "zh_TW.UTF-8",
        "FONTCONFIG_FILE": str(DEMO / "fonts.conf"),
        "DISPLAY": DISPLAY,
        "TERM": "xterm-256color",
        "PYTHONUNBUFFERED": "1",
        "ALACRITTY_LOG": str(DEMO / "alacritty.log"),
    }


def _window_box(title: str) -> tuple[int, int, int, int]:
    deadline = time.time() + 15
    while time.time() < deadline:
        result = subprocess.run(
            ["xdotool", "search", "--name", title, "getwindowgeometry", "--shell"],
            check=False,
            env={**os.environ, "DISPLAY": DISPLAY},
            text=True,
            capture_output=True,
        )
        values: dict[str, int] = {}
        for line in result.stdout.splitlines():
            if "=" in line:
                key, raw = line.split("=", 1)
                if raw.isdigit():
                    values[key] = int(raw)
        if {"X", "Y", "WIDTH", "HEIGHT"} <= values.keys() and values["WIDTH"] > 100:
            return values["X"], values["Y"], values["WIDTH"], values["HEIGHT"]
        time.sleep(0.1)
    raise SystemExit(f"no terminal window titled {title}")


def _capture_scene(stem: str, commands: list[str]) -> None:
    transcript = DEMO / "transcripts" / f"{stem}.txt"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    done = DEMO / f"{stem}.done"
    script = DEMO / f"{stem}.sh"
    title = f"metamuse-{stem}"
    if transcript.exists():
        transcript.unlink()
    if done.exists():
        done.unlink()
    script.write_text(_scene_script(commands, transcript, done), encoding="utf-8")
    script.chmod(0o700)
    config = DEMO / "alacritty.toml"
    config.write_text(
        (DEMO / "alacritty.base.toml")
        .read_text(encoding="utf-8")
        .replace('title = "metamuse-scene"', f'title = "{title}"'),
        encoding="utf-8",
    )
    env = _scene_env()
    # Absolute paths: the scene environment's PATH is only the demo bin plus
    # /usr/bin and /bin, and it must not grow to include a home directory.
    # stdbuf keeps bash line-buffered after `exec > >(tee ...)`, so the grab
    # sees the scene while the shell is still waiting.
    alacritty = shutil.which("alacritty")
    stdbuf = shutil.which("stdbuf")
    bash = shutil.which("bash")
    if not alacritty or not stdbuf or not bash:
        raise SystemExit("capture needs alacritty, stdbuf, and bash on PATH")
    proc = subprocess.Popen(
        [
            alacritty,
            "--config-file",
            str(config),
            "-e",
            stdbuf,
            "-oL",
            "-eL",
            bash,
            "--noprofile",
            "--norc",
            str(script),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 180
        while time.time() < deadline:
            if done.exists() and transcript.is_file() and transcript.stat().st_size > 0:
                break
            if proc.poll() is not None:
                raise SystemExit(f"{stem} terminal exited before the scene finished")
            time.sleep(0.15)
        else:
            raise SystemExit(f"{stem} did not finish")
        time.sleep(0.4)
        box = _window_box(title)
        grabbed = ImageGrab.grab(xdisplay=DISPLAY)
        left, top, width, height = box
        cropped = grabbed.crop((left, top, left + width, top + height))
        cropped.save(OUT / f"{stem}.png")
        shutil.copyfile(transcript, OUT / f"{stem}.txt")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    text = (OUT / f"{stem}.txt").read_text(encoding="utf-8")
    failures = privacy_failures(text)
    if failures:
        raise SystemExit(f"{stem} privacy scan failed: {failures}")
    visual = _visual_lines(text, COLUMNS)
    print(f"{stem} visual_lines={visual}", flush=True)
    if visual + 1 > LINES:
        raise SystemExit(f"{stem} uses {visual} visual lines plus the cursor; terminal has {LINES}")


def _git(args: list[str]) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, check=True, text=True, capture_output=True
    ).stdout.strip()


def _tool_versions() -> dict[str, str]:
    python = _run(
        [str(DEMO / "bin" / "python3"), "-c", "import sys; print(sys.version.split()[0])"]
    )
    return {
        "python": python.splitlines()[0].strip(),
        "pillow": Image.__version__,
        "pillow_xcb": str(bool(getattr(Image.core, "HAVE_XCB", False))),
        "jq": _run(["jq", "--version"]).strip(),
        "alacritty": _run(["alacritty", "--version"]).strip(),
        "xvfb": "xvfb " + _run(["dpkg-query", "-W", "-f", "${Version}", "xvfb"]).strip(),
        "mcp": _run(
            [
                str(DEMO / "bin" / "python3"),
                "-c",
                "from importlib.metadata import version; print(version('mcp'))",
            ]
        ).strip(),
        "metamuse": _run(
            [
                str(DEMO / "bin" / "python3"),
                "-c",
                "from importlib.metadata import version; print(version('metamuse-cli'))",
            ]
        ).strip(),
    }


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def _write_readme(images: list[dict[str, str]]) -> None:
    rows = []
    for scene, record in zip(SCENES, images, strict=True):
        stem, summary, commands = scene
        command_block = "<br>".join(f"`{_cell(cmd.replace('\\$', '$'))}`" for cmd in commands)
        rows.append(f"| `{record['png']}` | {stem} | {_cell(summary)} | {command_block} |")
    body = f"""# Terminal captures

These PNGs are crops of a real Alacritty window on Xvfb. The shell prints each
command, then runs the installed `metamuse` binary against `/tmp/metamuse-demo`.
No pixel inside the window was painted over. Cropping is the window rectangle
from `xdotool`.

Data is the committed synthetic corpus (`tests/fixtures/corpus/import/comments_mixed_bad.jsonl`
and `tests/fixtures/corpus/security/secrets.jsonl`) copied under `/tmp/metamuse-demo/inputs`.
Muse is configured `backend = "fixture"`. No account, credential, or network call is used.

The text file next to each PNG is the terminal transcript and the accessible
text alternative. `manifest.json` records hashes and tool versions.

## Frames

| Image | Scene | What the reader sees | Commands |
|---|---|---|---|
{chr(10).join(rows)}

## Reproduce

From the repository root, with the project virtualenv already synced:

```sh
uv run --script scripts/capture_site.py
```

The script refuses a transcript that contains `$USER`, the hostname, `/home/`,
or a line from `$SITE_PRIVATE_DENYLIST` when that file is set. A scene that
scrolls past the fixed {COLUMNS}x{LINES} window fails the capture.
"""
    (OUT / "README.md").write_text(body, encoding="utf-8")


def main() -> None:
    if not (REPO / ".venv" / "bin" / "metamuse").is_file():
        raise SystemExit("sync the project first so .venv/bin/metamuse exists")
    _prepare_demo()
    shutil.copyfile(DEMO / "alacritty.toml", DEMO / "alacritty.base.toml")
    OUT.mkdir(parents=True, exist_ok=True)
    tools = _tool_versions()
    xvfb = subprocess.Popen(
        ["Xvfb", DISPLAY, "-screen", "0", SCREEN, "-ac", "+extension", "RANDR"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.4)
    try:
        for stem, _summary, commands in SCENES:
            print(f"capturing {stem}", flush=True)
            _capture_scene(stem, commands)
    finally:
        xvfb.terminate()
        try:
            xvfb.wait(timeout=5)
        except subprocess.TimeoutExpired:
            xvfb.kill()
    captured = datetime.now().astimezone()
    images = []
    for stem, _summary, _commands in SCENES:
        png = OUT / f"{stem}.png"
        transcript = OUT / f"{stem}.txt"
        images.append(
            {
                "png": png.name,
                "transcript": transcript.name,
                "sha256": _sha256(png),
                "transcript_sha256": _sha256(transcript),
            }
        )
    manifest = {
        "source_commit": _git(["rev-parse", "HEAD"]),
        "source_tree": _git(["rev-parse", "HEAD^{tree}"]),
        "captured_at": captured.isoformat(timespec="seconds"),
        "timezone": captured.tzname() or "",
        "timezone_name": str(captured.tzinfo),
        "terminal": {
            "emulator": "alacritty",
            "version": tools["alacritty"],
            "font": "DejaVu Sans Mono 13, fallback Noto Sans CJK TC",
            "geometry": f"{COLUMNS}x{LINES}",
            "xvfb_screen": SCREEN,
            "colors": {"background": "#14211c", "foreground": "#e7f2ea"},
        },
        "tools": tools,
        "synthetic_data": True,
        "live_or_real_account": False,
        "image_edited": False,
        "crop_note": (
            "Cropped to the Alacritty window rectangle from xdotool. "
            "Pixels inside that rectangle are the unedited framebuffer, saved as PNG."
        ),
        "images": images,
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _write_readme(images)
    print(f"wrote {len(images)} frames under {OUT}")


if __name__ == "__main__":
    main()
