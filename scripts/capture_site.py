#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow==12.1.1"]
# ///
"""Capture actual CLI commands in Alacritty, at 2x density, on a private Xvfb.

The only pixel operation is a full-width crop with one empty terminal row below
content. PNG compression is verified against the captured RGB bytes.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from site_lib import privacy_failures, sha256_file  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DEMO = Path("/tmp/metamuse-demo")
OUT = REPO / "site" / "images"
SCREEN = "2800x2200x24"
SCALE = 2
BACKGROUND = "#14211c"
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


@dataclass(frozen=True)
class Scene:
    stem: str
    summary: str
    acceptance: tuple[str, ...]
    commands: tuple[str, ...]
    columns: int = 98
    lines: int = 42
    font_size: float = 13.0
    max_content_rows: int = 40
    expected_partial_commands: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(set(self.expected_partial_commands)) != len(self.expected_partial_commands):
            raise ValueError(f"{self.stem}: duplicate expected partial declaration")
        for command in self.expected_partial_commands:
            if self.commands.count(command) != 1:
                raise ValueError(f"{self.stem}: expected partial must name exactly one command")
        if self.expected_partial_commands and not re.search(r"\bpartial\b", self.summary, re.I):
            raise ValueError(f"{self.stem}: summary must explicitly mention a partial result")


# Add future approved scenes here. No application shortcuts or fabricated output:
# each command is printed literally and evaluated by bash in the real terminal.
SCENES = (
    Scene(
        stem="01-setup",
        summary="Offline init, explicit fixture config, and doctor capability rows.",
        acceptance=("AT-001",),
        commands=(
            "cd /tmp/metamuse-demo",
            "metamuse --workspace ws init",
            "python3 configure_demo_workspace.py",
            "sed -n '/^network_mode/p;/^backend/p' ws/metamuse.toml",
            "metamuse --workspace ws doctor > out/doctor.json",
            (
                "jq -cC '{network_mode, worker_available}, (.capabilities[] | "
                "[.name, .verification, .effective])' out/doctor.json"
            ),
        ),
        columns=98,
        lines=42,
        font_size=13.0,
        max_content_rows=40,
    ),
    Scene(
        stem="02-queue",
        summary=(
            "Accepted and queued without a worker; run once, then partial "
            "with 7 committed and 7 rejected."
        ),
        acceptance=("AT-003", "AT-008"),
        commands=(
            (
                "metamuse --workspace ws project create --name 'Synthetic demo' "
                "--request-key project > out/project.json"
            ),
            "P=$(jq -r .id out/project.json)",
            (
                'metamuse --workspace ws --json add --project "$P" --file '
                "inputs/mixed.jsonl --kind jsonl --request-key import > "
                "out/add.json"
            ),
            (
                "jq -C '{status, state: .data.state, worker_available: "
                ".data.worker_available}' out/add.json"
            ),
            "J=$(jq -r .data.job_id out/add.json)",
            "metamuse --workspace ws worker run --once",
            'metamuse --workspace ws jobs get "$J" > out/job.json',
            "jq -C '.job | {state, coverage_status}' out/job.json",
            "jq -cC '.steps[0].result.observed_scope | {committed, rejected}' out/job.json",
            (
                "jq -cC '.steps[0].result.data.rows[] | select(.status == "
                '"rejected") | {line, code}\' out/job.json'
            ),
        ),
        columns=98,
        lines=42,
        font_size=13.0,
        max_content_rows=40,
    ),
    Scene(
        stem="03-search",
        summary="One exact item revision, with file_import and synthetic_fixture source labels.",
        acceptance=("AT-017",),
        commands=(
            (
                'metamuse --workspace ws search --project "$P" --query 正常留言第一行 '
                "--limit 1 > out/search.json"
            ),
            "jq -C '.results[0] | {ref, snippet, source}' out/search.json",
        ),
        columns=98,
        lines=42,
        font_size=13.0,
        max_content_rows=40,
    ),
    Scene(
        stem="04-packet",
        summary="Exact packet text, one url_token redaction, and its SHA-256. Nothing is sent.",
        acceptance=("AT-015", "AT-028"),
        commands=(
            (
                'metamuse --workspace ws add --project "$P" --file '
                "inputs/secrets.jsonl --kind jsonl --request-key secrets --run > "
                "out/secrets.json"
            ),
            (
                'metamuse --workspace ws search --project "$P" --query 存取網址 '
                "--limit 1 > out/secret-search.json"
            ),
            "jq '[.results[0].ref]' out/secret-search.json > inputs/selection.json",
            (
                'metamuse --workspace ws packet prepare --project "$P" '
                "--selection-file inputs/selection.json --request-key packet > "
                "out/packet.json"
            ),
            "jq -r .rendered_text out/packet.json",
            "jq -C '{redactions, digest}' out/packet.json",
            "K=$(jq -r .id out/packet.json)",
        ),
        columns=98,
        lines=42,
        font_size=13.0,
        max_content_rows=40,
    ),
    Scene(
        stem="05-approval-preview",
        summary="A grant-free Muse open returns E_SCOPE_REQUIRED with the exact action preview.",
        acceptance=("AT-016", "AT-020"),
        commands=(
            (
                "metamuse --workspace ws --json --non-interactive muse open "
                '--project "$P" --topic "Synthetic review" --request-key open > '
                "out/open-review.json || test $? -eq 3"
            ),
            (
                "jq -C '{status, error: .error.code, approval: .approval | "
                "{operation, action_digest, destination, topic, request}}' "
                "out/open-review.json"
            ),
            (
                'read R A < <(jq -r ".approval | [.request_file, .action_digest] '
                '| @tsv" out/open-review.json)'
            ),
            'jq .approval.request out/open-review.json > "$R"',
        ),
        columns=98,
        lines=42,
        font_size=13.0,
        max_content_rows=40,
    ),
    Scene(
        stem="06-human-approval",
        summary=(
            "The operator previews the packet and approves the exact digest; Muse open is accepted."
        ),
        acceptance=("AT-015", "AT-016", "AT-020"),
        commands=(
            'metamuse --workspace ws packet preview "$K" > out/packet-preview.json',
            "jq -r .rendered_text out/packet-preview.json",
            (
                "metamuse --workspace ws --json permit approve --operation "
                'muse.open --request-file "$R" --exact-digest "$A" --request-key '
                "open-grant > out/open-grant.json"
            ),
            "G=$(jq -r .data.id out/open-grant.json)",
            (
                'metamuse --workspace ws --json muse open --project "$P" --topic '
                '"Synthetic review" --access-grant "$G" --request-key open > '
                "out/open.json"
            ),
            (
                "jq -C '{status, data: .data | {state, discussion_id, "
                "worker_available}}' out/open.json"
            ),
            "T=$(jq -r .data.discussion_id out/open.json)",
        ),
        columns=98,
        lines=42,
        font_size=13.0,
        max_content_rows=40,
    ),
    Scene(
        stem="07-fixture-discussion",
        summary=(
            "Approved fixture question, worker refresh, cached reply and "
            "explicit needs_more_data closure."
        ),
        acceptance=("AT-016", "AT-020", "AT-021", "AT-022", "AT-024"),
        commands=(
            "metamuse --workspace ws worker run --once > out/w-open.json",
            "printf '%s' '這份資料有哪些資訊缺口？' > inputs/q1.txt",
            (
                'metamuse --workspace ws --json muse send --discussion "$T" '
                '--packet "$K" --question-file inputs/q1.txt --request-key '
                "send-1 > out/s1-review.json || test $? -eq 3"
            ),
            'metamuse --workspace ws packet preview "$K" > out/p1.json',
            (
                'read R A < <(jq -r ".approval | [.request_file, .action_digest] '
                '| @tsv" out/s1-review.json)'
            ),
            'jq .approval.request out/s1-review.json > "$R"',
            (
                "metamuse --workspace ws --json permit approve --operation "
                'muse.send --request-file "$R" --exact-digest "$A" --request-key '
                "sg1 > out/sg1.json 2> out/sg1.txt"
            ),
            "G=$(jq -r .data.id out/sg1.json)",
            (
                'metamuse --workspace ws --json muse send --discussion "$T" '
                '--packet "$K" --question-file inputs/q1.txt --share-grant "$G" '
                "--request-key send-1 > out/s1.json"
            ),
            "E=$(jq -r .data.exchange_id out/s1.json)",
            "metamuse --workspace ws worker run --once > out/ws1.json",
            (
                'metamuse --workspace ws --json muse read --exchange "$E" '
                "--refresh > out/r1-review.json || test $? -eq 3"
            ),
            (
                'read R A < <(jq -r ".approval | [.request_file, .action_digest] '
                '| @tsv" out/r1-review.json)'
            ),
            'jq .approval.request out/r1-review.json > "$R"',
            (
                "metamuse --workspace ws --json permit approve --operation "
                'muse.read --request-file "$R" --exact-digest "$A" --request-key '
                "rg1 > out/rg1.json 2> out/rg1.txt"
            ),
            "G=$(jq -r .data.id out/rg1.json)",
            (
                'metamuse --workspace ws muse read --exchange "$E" --refresh '
                '--access-grant "$G" > out/rf1.json'
            ),
            "metamuse --workspace ws worker run --once > out/wr1.json",
            'metamuse --workspace ws --json muse read --exchange "$E" > out/r1.json',
            (
                "jq -cC '.data | {discussion_id, exchange_id, backend, mock, "
                "research_complete}' out/r1.json"
            ),
            (
                'jq -cC \'.data.messages[] | select(.role == "assistant") | '
                "{role, text, association}' out/r1.json"
            ),
            (
                'metamuse --workspace ws muse close-exchange "$E" --outcome '
                "needs_more_data --request-key close-1 > out/c1.json"
            ),
            "jq -cC '{outcome, research_complete}' out/c1.json",
        ),
        columns=98,
        lines=42,
        font_size=13.0,
        max_content_rows=40,
    ),
    Scene(
        stem="08-fixture-follow-up",
        summary=(
            "Approved fixture question, worker refresh, cached reply and "
            "explicit needs_more_data closure."
        ),
        acceptance=("AT-016", "AT-020", "AT-021", "AT-022", "AT-024"),
        commands=(
            "printf '%s' '哪些缺口會影響這個判斷？' > inputs/q2.txt",
            (
                'metamuse --workspace ws --json muse send --discussion "$T" '
                '--packet "$K" --question-file inputs/q2.txt --request-key '
                "send-2 > out/s2-review.json || test $? -eq 3"
            ),
            'metamuse --workspace ws packet preview "$K" > out/p2.json',
            (
                'read R A < <(jq -r ".approval | [.request_file, .action_digest] '
                '| @tsv" out/s2-review.json)'
            ),
            'jq .approval.request out/s2-review.json > "$R"',
            (
                "metamuse --workspace ws --json permit approve --operation "
                'muse.send --request-file "$R" --exact-digest "$A" --request-key '
                "sg2 > out/sg2.json 2> out/sg2.txt"
            ),
            "G=$(jq -r .data.id out/sg2.json)",
            (
                'metamuse --workspace ws --json muse send --discussion "$T" '
                '--packet "$K" --question-file inputs/q2.txt --share-grant "$G" '
                "--request-key send-2 > out/s2.json"
            ),
            "E=$(jq -r .data.exchange_id out/s2.json)",
            "metamuse --workspace ws worker run --once > out/ws2.json",
            (
                'metamuse --workspace ws --json muse read --exchange "$E" '
                "--refresh > out/r2-review.json || test $? -eq 3"
            ),
            (
                'read R A < <(jq -r ".approval | [.request_file, .action_digest] '
                '| @tsv" out/r2-review.json)'
            ),
            'jq .approval.request out/r2-review.json > "$R"',
            (
                "metamuse --workspace ws --json permit approve --operation "
                'muse.read --request-file "$R" --exact-digest "$A" --request-key '
                "rg2 > out/rg2.json 2> out/rg2.txt"
            ),
            "G=$(jq -r .data.id out/rg2.json)",
            (
                'metamuse --workspace ws muse read --exchange "$E" --refresh '
                '--access-grant "$G" > out/rf2.json'
            ),
            "metamuse --workspace ws worker run --once > out/wr2.json",
            'metamuse --workspace ws --json muse read --exchange "$E" > out/r2.json',
            (
                "jq -cC '.data | {discussion_id, exchange_id, backend, mock, "
                "research_complete}' out/r2.json"
            ),
            (
                'jq -cC \'.data.messages[] | select(.role == "assistant") | '
                "{role, text, association}' out/r2.json"
            ),
            (
                'metamuse --workspace ws muse close-exchange "$E" --outcome '
                "needs_more_data --request-key close-2 > out/c2.json"
            ),
            "jq -cC '{outcome, research_complete}' out/c2.json",
        ),
        columns=98,
        lines=42,
        font_size=13.0,
        max_content_rows=40,
    ),
    Scene(
        stem="09-host-draft",
        summary=(
            "Host text stored unchanged, citing the item revision and both fixture observations."
        ),
        acceptance=("AT-023", "AT-024"),
        commands=(
            (
                "printf '%s\\n' '活動紀錄（host 撰寫）' "  # Preserve the host's two lines.
                "'首輪與追問仍有缺口，尚未完成研究。' > inputs/draft.txt"
            ),
            (
                "jq '[.results[0].ref] + [inputs | "
                ".data.observations[-1].evidence_ref]' out/search.json "
                "out/r1.json out/r2.json > inputs/facts.json"
            ),
            (
                'metamuse --workspace ws draft save --project "$P" --text-file '
                "inputs/draft.txt --platform generic --facts-file "
                "inputs/facts.json --request-key draft > out/draft.json"
            ),
            "jq -C '{draft_id, revision, text}' out/draft.json",
            "jq -cC '.facts_refs[]' out/draft.json",
        ),
        columns=98,
        lines=42,
        font_size=13.0,
        max_content_rows=40,
    ),
    Scene(
        stem="10-local-export",
        summary=(
            "Local bundle manifest and the CLI's no-publish, no-upload, redaction-guard warnings."
        ),
        acceptance=("AT-025",),
        commands=(
            "D=$(jq -r .draft_id out/draft.json)",
            (
                'jq -n --arg id "$D" \'[{kind:"draft_revision", id:$id, '
                "revision:1}]' > inputs/export-refs.json"
            ),
            (
                'metamuse --workspace ws export --project "$P" --refs-file '
                "inputs/export-refs.json --format bundle --request-key export > "
                "out/export.json"
            ),
            (
                "jq -C '.manifest | {format, backend, mock, research_complete, "
                "text_policy, asset_policy, redaction_notice}' out/export.json"
            ),
            "jq -cC '.manifest.files[]' out/export.json",
        ),
        columns=98,
        lines=42,
        font_size=13.0,
        max_content_rows=40,
    ),
    Scene(
        stem="11-mcp-worker",
        summary=(
            "The official client discovers 15 tools, collects a synthetic "
            "file and polls the attached worker."
        ),
        acceptance=("AT-008", "AT-026"),
        commands=("python3 mcp_demo.py",),
        columns=98,
        lines=42,
        font_size=13.0,
        max_content_rows=40,
    ),
    Scene(
        stem="12-skill-export",
        summary="Skill export writes SKILL.md and references to the named local destination.",
        acceptance=("AT-027",),
        commands=("metamuse skill export --dest host-skill", "ls -R host-skill"),
        columns=98,
        lines=42,
        font_size=13.0,
        max_content_rows=40,
    ),
)

HERO = Scene(
    "00-hero",
    "The same packet's exact text, redaction count, and digest, captured for the hero.",
    ("AT-015", "AT-028"),
    ("jq -r .rendered_text out/packet.json", "jq -cC '{redactions, digest}' out/packet.json"),
    columns=60,
    lines=24,
    font_size=16.0,
    max_content_rows=22,
)


def _run(cmd: list[str], **kwargs) -> str:
    result = subprocess.run(cmd, check=True, text=True, capture_output=True, **kwargs)
    return result.stdout.strip()


def _width(text: str) -> int:
    return sum(
        0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in "WF" else 1
        for char in ANSI.sub("", text)
    )


def _visual_lines(text: str, columns: int) -> int:
    return sum(max(1, (_width(line) + columns - 1) // columns) for line in text.splitlines())


def _scene_script(scene: Scene, transcript: Path, done: Path) -> str:
    commands = "\n".join(
        f"run {position} {5 if cmd in scene.expected_partial_commands else 0} {shlex.quote(cmd)}"
        for position, cmd in enumerate(scene.commands, start=1)
    )
    return f"""#!/bin/bash
set -u
set -o pipefail
cd /tmp/metamuse-demo
if [ -f out/state.sh ]; then source out/state.sh; fi
exec > >(tee {shlex.quote(str(transcript))}) 2>&1
printf '\\033[?25l'
run() {{
  local position="$1" expected="$2" command="$3" code partial=no
  if [ "$expected" -eq 5 ]; then partial=yes; fi
  printf '\\033[32m$\\033[0m %s\\n' "$command"
  eval "$command"
  code=$?
  if [ "$code" -ne "$expected" ]; then
    printf '%s: command %s/%s exited %s (expected 5: %s)\\n' \\
      {shlex.quote(scene.stem)} "$position" {len(scene.commands)} "$code" "$partial"
    exit 1
  fi
}}
{commands}
declare -p P J D K R A G T E > out/state.sh 2>/dev/null || true
touch {shlex.quote(str(done))}
exec sleep 45
"""


def _prepare_demo() -> None:
    if DEMO.exists():
        shutil.rmtree(DEMO)
    for name in ("home", "tmp", "inputs", "out", "bin", "transcripts"):
        (DEMO / name).mkdir(parents=True)
    venv_bin = REPO / ".venv" / "bin"
    # Keep the virtualenv path off screen; resolving a Python symlink loses the venv.
    python = DEMO / "bin" / "python3"
    python.write_text(f'#!/bin/sh\nexec {shlex.quote(str(venv_bin / "python3"))} "$@"\n')
    python.chmod(0o755)
    (DEMO / "bin" / "metamuse").symlink_to(venv_bin / "metamuse")
    corpus = REPO / "tests" / "fixtures" / "corpus"
    shutil.copyfile(corpus / "import" / "comments_mixed_bad.jsonl", DEMO / "inputs/mixed.jsonl")
    # A shorter synthetic URL keeps access_token=[REDACTED] together in the hero.
    # This changes INPUT data only; the CLI output and framebuffer are unedited.
    secrets = (corpus / "security" / "secrets.jsonl").read_text()
    secrets = secrets.replace(
        "API 存取網址測試：https://example.org/api/v1/feed?",
        "存取網址：https://example.org/?",
    )
    (DEMO / "inputs/secrets.jsonl").write_text(secrets)
    # The MCP frame imports a separate redistributable synthetic file.
    mcp_rows = (corpus / "import/comments_tree.jsonl").read_text().splitlines()[:3]
    (DEMO / "inputs/mcp.jsonl").write_text("\n".join(mcp_rows) + "\n")
    for name in ("configure_demo_workspace.py", "mcp_demo.py"):
        shutil.copyfile(REPO / "scripts" / name, DEMO / name)
    (DEMO / "fonts.conf").write_text(
        '<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n'
        '<fontconfig><include ignore_missing="yes">/etc/fonts/fonts.conf</include>\n'
        "<alias><family>DejaVu Sans Mono</family><prefer>"
        "<family>DejaVu Sans Mono</family><family>Noto Sans CJK TC</family>"
        "</prefer></alias></fontconfig>\n"
    )


def _config(scene: Scene) -> str:
    return f"""[general]
live_config_reload = false
working_directory = "/tmp/metamuse-demo"
[window]
decorations = "None"
dynamic_title = false
title = "metamuse-{scene.stem}"
dimensions = {{ columns = {scene.columns}, lines = {scene.lines} }}
padding = {{ x = 12, y = 10 }}
[font]
size = {scene.font_size}
[font.normal]
family = "DejaVu Sans Mono"
[font.bold]
family = "DejaVu Sans Mono"
style = "Bold"
[colors.primary]
background = "{BACKGROUND}"
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
"""


def _scene_env(display: str) -> dict[str, str]:
    return {
        "HOME": str(DEMO / "home"),
        "TMPDIR": str(DEMO / "tmp"),
        "PATH": f"{DEMO / 'bin'}:/usr/bin:/bin",
        "LANG": "zh_TW.UTF-8",
        "LC_ALL": "zh_TW.UTF-8",
        "FONTCONFIG_FILE": str(DEMO / "fonts.conf"),
        "DISPLAY": display,
        "WINIT_X11_SCALE_FACTOR": str(SCALE),
        "TERM": "xterm-256color",
        "PYTHONUNBUFFERED": "1",
    }


def _start_xvfb() -> tuple[subprocess.Popen, str]:
    # Check sockets AND lockfiles. Never connect to an existing display.
    for number in range(60, 200):
        if Path(f"/tmp/.X11-unix/X{number}").exists() or Path(f"/tmp/.X{number}-lock").exists():
            continue
        display = f":{number}"
        proc = subprocess.Popen(
            ["Xvfb", display, "-screen", "0", SCREEN, "-nolisten", "tcp", "-ac"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(30):
            if proc.poll() is not None:
                break  # Lost a race for this number: try the next free display.
            if Path(f"/tmp/.X11-unix/X{number}").exists():
                return proc, display
            time.sleep(0.1)
        _stop(proc)
    raise SystemExit("could not start Xvfb on a free display")


def _stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _window_box(title: str, env: dict) -> dict[str, int]:
    output = _run(
        ["xdotool", "search", "--name", f"^{title}$", "getwindowgeometry", "--shell"], env=env
    )
    values = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    return {
        key: int(values[name])
        for key, name in zip(
            ("x", "y", "width", "height"), ("X", "Y", "WIDTH", "HEIGHT"), strict=True
        )
    }


def _capture_scene(scene: Scene, display: str, staged: Path) -> dict:
    from PIL import Image, ImageChops, ImageGrab

    for command in scene.commands:
        if _visual_lines("$ " + command, scene.columns) > 2:
            raise SystemExit(f"{scene.stem}: command exceeds two rows: {command}")
    transcript = DEMO / "transcripts" / f"{scene.stem}.txt"
    done = DEMO / f"{scene.stem}.done"
    script = DEMO / f"{scene.stem}.sh"
    script.write_text(_scene_script(scene, transcript, done))
    config = DEMO / f"{scene.stem}.toml"
    config.write_text(_config(scene))
    env = _scene_env(display)
    proc = subprocess.Popen(
        [
            shutil.which("alacritty"),
            "--config-file",
            str(config),
            "-e",
            "stdbuf",
            "-oL",
            "-eL",
            "bash",
            "--noprofile",
            "--norc",
            str(script),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 40
        while not done.exists():
            if proc.poll() is not None or time.monotonic() >= deadline:
                raise SystemExit(f"{scene.stem} failed: inspect the demo transcript")
            time.sleep(0.1)
        time.sleep(0.5)
        text = ANSI.sub("", transcript.read_text())
        failures = privacy_failures(text)
        if failures:
            raise SystemExit(f"{scene.stem} privacy scan failed: {failures}")
        visual_rows = _visual_lines(text, scene.columns)
        if visual_rows > scene.max_content_rows:
            raise SystemExit(f"{scene.stem}: {visual_rows} rows exceeds {scene.max_content_rows}")
        box = _window_box(f"metamuse-{scene.stem}", env)
        left, top, width, height = (box[key] for key in ("x", "y", "width", "height"))
        grabbed = ImageGrab.grab(xdisplay=display).convert("RGB")
        if left < 0 or top < 0 or left + width > grabbed.width or top + height > grabbed.height:
            raise SystemExit("terminal window extends outside the virtual screen")
        window = grabbed.crop((left, top, left + width, top + height))
        padding_y = 10 * SCALE
        cell_height, remainder = divmod(height - 2 * padding_y, scene.lines)
        cell_width, width_remainder = divmod(width - 24 * SCALE, scene.columns)
        if remainder or width_remainder:
            raise SystemExit("window geometry does not match the configured 2x grid")
        ink = ImageChops.difference(window, Image.new("RGB", window.size, BACKGROUND)).getbbox()
        if not ink:
            raise SystemExit("empty terminal framebuffer")
        content_rows = (ink[3] - 1 - padding_y) // cell_height + 1
        if content_rows != visual_rows:
            raise SystemExit(
                f"{scene.stem}: framebuffer rows {content_rows} != transcript rows {visual_rows}"
            )
        crop_height = padding_y + (content_rows + 1) * cell_height
        crop = {**box, "height": crop_height}
        cropped = window.crop((0, 0, width, crop_height))
        png = staged / f"{scene.stem}.png"
        cropped.save(png, optimize=True, compress_level=9)
        with Image.open(png) as decoded:
            identical = decoded.convert("RGB").tobytes() == cropped.tobytes()
        if not identical:
            raise SystemExit("PNG optimization changed decoded pixels")
        (staged / f"{scene.stem}.txt").write_text(text)
        record = {
            "png": png.name,
            "transcript": f"{scene.stem}.txt",
            "commands": list(scene.commands),
            "acceptance": list(scene.acceptance),
            "columns": scene.columns,
            "window_rows": scene.lines,
            "content_rows": content_rows,
            "font_size_pt": scene.font_size,
            "cell_px": {"width": cell_width, "height": cell_height},
            "padding_top_px": padding_y,
            "window_rect": box,
            "crop_rect": crop,
            "bytes": png.stat().st_size,
            "sha256": sha256_file(png),
            "transcript_sha256": sha256_file(staged / f"{scene.stem}.txt"),
            "lossless_verified": identical,
            "decoded_rgb_sha256": hashlib.sha256(cropped.tobytes()).hexdigest(),
        }
        print(
            f"{scene.stem}: {width}x{crop_height}, {content_rows} rows, {record['bytes']} bytes",
            flush=True,
        )
        return record
    finally:
        _stop(proc)


def _versions() -> dict[str, str]:
    from PIL import Image

    return {
        "python": _run(
            [str(DEMO / "bin/python3"), "-c", "import sys; print(sys.version.split()[0])"]
        ),
        "pillow": Image.__version__,
        "jq": _run(["jq", "--version"]),
        "alacritty": _run(["alacritty", "--version"]),
        "xvfb": _run(["dpkg-query", "-W", "-f", "${Version}", "xvfb"]),
        "mcp": _run(
            [
                str(DEMO / "bin/python3"),
                "-c",
                "from importlib.metadata import version; print(version('mcp'))",
            ]
        ),
        "metamuse": _run(
            [
                str(DEMO / "bin/python3"),
                "-c",
                "from importlib.metadata import version; print(version('metamuse-cli'))",
            ]
        ),
    }


def _write_readme(records: list[dict], destination: Path) -> None:
    parts = [
        """# Terminal captures

Real Alacritty on a newly allocated Xvfb display; `WINIT_X11_SCALE_FACTOR=2`.
The shell prints each literal command and executes it from `/tmp/metamuse-demo`.
Variables set visibly in earlier scenes are retained between frames.

Each PNG keeps the full window width and its original top edge, ending one empty
terminal row below the last ink. Pixels within that rectangle are unedited.
Pillow's lossless PNG optimization is checked by decoding and comparing all RGB
bytes. The manifest records window/crop rectangles in display coordinates,
cell dimensions, content rows, commands, AT IDs, hashes, sizes and tool versions.

The CLI inputs use the repository's synthetic `comments_mixed_bad.jsonl` and
`secrets.jsonl` fixtures. In the copied secrets input, the first row's synthetic URL
is shortened from `https://example.org/api/v1/feed?` to `https://example.org/?`
and its label to `存取網址：` so the redaction fits together at 60 columns.
The synthetic token and source labels are unchanged; this is input data, not an
edit to CLI output. Muse is explicitly configured with `backend = "fixture"`.
No credentials, real account, external network or publication is involved.
Muse sends go only to the offline fixture backend. Operator approvals are scripted
terminal input in this reproducible synthetic demo; a host has no MCP approval tool.
Full send/read operator reviews are redirected to demo out/ files to keep their
frames compact; the open approval frame displays the real stderr review in full.
The MCP input is the first three rows of the synthetic comments_tree.jsonl fixture.
The text file beside every image is its accessible transcript, linked from both
languages on the page. The hero is a separate 60-column capture of the same packet.

## Frames
"""
    ]
    scenes = {scene.stem: scene for scene in (*SCENES, HERO)}
    for record in records:
        scene = scenes[Path(record["png"]).stem]
        parts.append(
            f"\n### {record['png']} ({', '.join(scene.acceptance)})\n\n{scene.summary}\n\n```sh\n"
            + "\n".join(scene.commands)
            + "\n```\n"
        )
    parts.append("""
## Reproduce

With Alacritty, Xvfb, xdotool, jq and the already-synced project virtualenv:

```sh
UV_OFFLINE=1 uv run --script scripts/capture_site.py
```

The capture uses a pinned Pillow script dependency; it is not a product dependency.
The script rejects local home paths, the operator's username or hostname, and any
term from `SITE_PRIVATE_DENYLIST`. Commands over two rows and scenes over their row
budget fail. The script stops its own terminal and virtual display on exit.

Skill export demonstrates installation output, not full AT-027 missing-backend
acceptance. MCP shows discovery, local collection, attached-worker polling and one search,
not every host/tool pairing. Fixture replies are progress, not completed research.
""")
    (destination / "README.md").write_text("".join(parts))


def _commit_outputs(staged: Path, records: list[dict], manifest: dict) -> None:
    """Prepare a complete, verified directory on OUT's filesystem, then publish it."""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".images-stage-", dir=OUT.parent) as directory:
        prepared = Path(directory)
        for record in records:
            for file_key, hash_key in (("png", "sha256"), ("transcript", "transcript_sha256")):
                name = record[file_key]
                target = prepared / name
                shutil.copy2(staged / name, target)
                if sha256_file(target) != record[hash_key]:
                    raise RuntimeError(f"capture output hash mismatch: {name}")
        # Verify the actual replacement files before creating its manifest.
        (prepared / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
        )
        _write_readme(records, prepared)

        backup = Path(tempfile.mkdtemp(prefix=".images-backup-", dir=OUT.parent))
        backup.rmdir()
        try:
            if OUT.exists():
                OUT.rename(backup)
            prepared.rename(OUT)
        except BaseException as error:
            try:
                if backup.exists() and not OUT.exists():
                    backup.rename(OUT)
            except BaseException as rollback_error:
                raise RuntimeError(
                    f"capture output commit and rollback failed; previous set in {backup.name}"
                ) from rollback_error
            raise RuntimeError(
                "capture output commit failed; output directory was not mixed"
            ) from error
        if backup.exists():
            try:
                shutil.rmtree(backup)
            except BaseException as error:
                raise RuntimeError(
                    f"capture output committed; backup cleanup failed: {backup.name}"
                ) from error


def _manifest(records: list[dict], display: str) -> dict:
    captured = datetime.now().astimezone()
    return {
        "source_commit": _run(["git", "rev-parse", "HEAD"], cwd=REPO),
        "source_tree": _run(["git", "rev-parse", "HEAD^{tree}"], cwd=REPO),
        "capture_script_sha256": sha256_file(Path(__file__)),
        "synthetic_inputs": [
            {
                "file": "inputs/mixed.jsonl",
                "source": "tests/fixtures/corpus/import/comments_mixed_bad.jsonl",
                "sha256": sha256_file(DEMO / "inputs/mixed.jsonl"),
            },
            {
                "file": "inputs/secrets.jsonl",
                "source": "tests/fixtures/corpus/security/secrets.jsonl",
                ("change"): (
                    "First row label shortened to 存取網址： and URL path shortened to "
                    "/; synthetic token unchanged"
                ),
                "sha256": sha256_file(DEMO / "inputs/secrets.jsonl"),
            },
            {
                "file": "inputs/mcp.jsonl",
                "source": "tests/fixtures/corpus/import/comments_tree.jsonl",
                "change": "First three rows only",
                "sha256": sha256_file(DEMO / "inputs/mcp.jsonl"),
            },
        ],
        "product_base_commit": _run(["git", "merge-base", "HEAD", "main"], cwd=REPO),
        "captured_at": captured.isoformat(timespec="seconds"),
        "scale_factor": SCALE,
        ("density_method"): (
            "WINIT_X11_SCALE_FACTOR=2; geometry verified against doubled padding and cell grid"
        ),
        "terminal": {
            "emulator": "Alacritty",
            "font": "DejaVu Sans Mono, Noto Sans CJK TC fallback",
            "xvfb_screen": SCREEN,
            "display": display,
        },
        "tools": _versions(),
        "scripted_operator_input": True,
        "operator_input_note": (
            "Synthetic terminal approvals use permit approve --exact-digest; "
            "no MCP approval tool or model-minted grant."
        ),
        "synthetic_data": True,
        "live_or_real_account": False,
        "image_edited": False,
        ("crop_note"): (
            "Full window width from its top edge through one empty cell "
            "row below the final ink; display-coordinate rectangles. Kept "
            "RGB pixels unchanged."
        ),
        ("png_optimization"): (
            "Pillow optimize=True, compress_level=9; decoded RGB equality checked per image"
        ),
        "images": records,
    }


def main() -> None:
    if not (REPO / ".venv/bin/metamuse").is_file():
        raise SystemExit("sync the project first")
    _prepare_demo()
    with tempfile.TemporaryDirectory(prefix="capture-site-", dir=DEMO / "tmp") as directory:
        staged = Path(directory)
        xvfb, display = _start_xvfb()
        records = []
        try:
            for scene in (*SCENES, HERO):
                records.append(_capture_scene(scene, display, staged))
        finally:
            _stop(xvfb)
        _commit_outputs(staged, records, _manifest(records, display))
    print(f"Wrote {len(records)} real frames.")


if __name__ == "__main__":
    main()
