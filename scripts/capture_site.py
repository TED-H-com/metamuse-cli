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
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageChops, ImageGrab

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


# Add future approved scenes here. No application shortcuts or fabricated output:
# each command is printed literally and evaluated by bash in the real terminal.
SCENES = (
    Scene(
        "01-setup",
        "Offline init, explicit fixture config, and doctor capability rows.",
        ("AT-001",),
        (
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
    ),
    Scene(
        "02-queue",
        (
            "Accepted and queued without a worker; run once, then partial "
            "with 7 committed and 7 rejected."
        ),
        ("AT-003", "AT-008"),
        (
            (
                "metamuse --workspace ws project create --name 'Synthetic "
                "demo' --request-key project > out/project.json"
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
    ),
    Scene(
        "03-search",
        "One exact item revision, with file_import and synthetic_fixture source labels.",
        ("AT-017",),
        (
            (
                'metamuse --workspace ws search --project "$P" --query '
                "正常留言第一行 --limit 1 > out/search.json"
            ),
            "jq -C '.results[0] | {ref, snippet, source}' out/search.json",
        ),
    ),
    Scene(
        "04-packet",
        "Exact packet text, one url_token redaction, and its SHA-256. Nothing is sent.",
        ("AT-015", "AT-028"),
        (
            (
                'metamuse --workspace ws add --project "$P" --file '
                "inputs/secrets.jsonl --kind jsonl --request-key secrets "
                "--run > out/secrets.json"
            ),
            (
                'metamuse --workspace ws search --project "$P" --query 存取網址 '
                "--limit 1 > out/secret-search.json"
            ),
            "jq '[.results[0].ref]' out/secret-search.json > inputs/selection.json",
            (
                'metamuse --workspace ws packet prepare --project "$P" '
                "--selection-file inputs/selection.json --request-key packet "
                "> out/packet.json"
            ),
            "jq -r .rendered_text out/packet.json",
            "jq -C '{redactions, digest}' out/packet.json",
        ),
    ),
    Scene(
        "05-draft",
        "Host-supplied text stored as written, citing an exact item revision; no model call.",
        ("AT-023",),
        (
            (
                "printf '%s\\n' '活動紀錄（host 撰寫）' "
                "'本地留言仍在。壞列沒有被算成完整匯入。' > inputs/draft.txt"
            ),
            "jq '[.results[0].ref]' out/search.json > inputs/facts.json",
            (
                'metamuse --workspace ws draft save --project "$P" '
                "--text-file inputs/draft.txt --platform generic --facts-file "
                "inputs/facts.json --request-key draft > out/draft.json"
            ),
            "jq -C '{draft_id, revision, text, facts_refs}' out/draft.json",
        ),
    ),
    Scene(
        "06-export",
        "Local bundle manifest and the CLI's no-publish, no-upload, redaction-guard warnings.",
        ("AT-025",),
        (
            "D=$(jq -r .draft_id out/draft.json)",
            (
                'jq -n --arg id "$D" \'[{kind:"draft_revision", id:$id, '
                "revision:1}]' > inputs/export-refs.json"
            ),
            (
                'metamuse --workspace ws export --project "$P" --refs-file '
                "inputs/export-refs.json --format bundle --request-key export "
                "> out/export.json"
            ),
            (
                "jq -C '.manifest | {format, text_policy, asset_policy, "
                "redaction_notice}' out/export.json"
            ),
            "jq -cC '.manifest.files[]' out/export.json",
        ),
    ),
    Scene(
        "07-mcp",
        "Official mcp stdio client lists 15 tools and readOnly hints, then calls metamuse_search.",
        ("AT-026",),
        ("python3 mcp_demo.py",),
    ),
    Scene(
        "08-skill",
        "Skill export writes SKILL.md and references to the named local destination.",
        ("AT-027",),
        ("metamuse skill export --dest host-skill", "ls -R host-skill"),
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
    commands = "\n".join(f"run {shlex.quote(cmd)}" for cmd in scene.commands)
    return f"""#!/bin/bash
set -u
set -o pipefail
cd /tmp/metamuse-demo
if [ -f out/state.sh ]; then source out/state.sh; fi
exec > >(tee {shlex.quote(str(transcript))}) 2>&1
printf '\\033[?25l'
run() {{
  printf '\\033[32m$\\033[0m %s\\n' "$1"
  eval "$1"
  code=$?
  if [ "$code" -ne 0 ] && [ "$code" -ne 5 ]; then
    printf 'command exited %s\\n' "$code"
    exit "$code"
  fi
}}
{commands}
declare -p P J D > out/state.sh 2>/dev/null || true
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


def _capture_scene(scene: Scene, display: str) -> dict:
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
        png = OUT / f"{scene.stem}.png"
        cropped.save(png, optimize=True, compress_level=9)
        with Image.open(png) as decoded:
            identical = decoded.convert("RGB").tobytes() == cropped.tobytes()
        if not identical:
            raise SystemExit("PNG optimization changed decoded pixels")
        (OUT / f"{scene.stem}.txt").write_text(text)
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
            "transcript_sha256": sha256_file(OUT / f"{scene.stem}.txt"),
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


def _write_readme(records: list[dict]) -> None:
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

Only the repository's synthetic `comments_mixed_bad.jsonl` and `secrets.jsonl`
fixtures are used. In the copied secrets input, the first row's synthetic URL
is shortened from `https://example.org/api/v1/feed?` to `https://example.org/?`
and its label to `存取網址：` so the redaction fits together at 60 columns.
The synthetic token and source labels are unchanged; this is input data, not an
edit to CLI output. Muse is explicitly configured with `backend = "fixture"`.
No credentials, real account, external network, send or publication is involved.
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
acceptance. MCP shows discovery and one search call, not every host/tool pairing.
""")
    (OUT / "README.md").write_text("".join(parts))


def main() -> None:
    if not (REPO / ".venv/bin/metamuse").is_file():
        raise SystemExit("sync the project first")
    _prepare_demo()
    OUT.mkdir(parents=True, exist_ok=True)
    xvfb, display = _start_xvfb()
    records = []
    try:
        for scene in (*SCENES, HERO):
            records.append(_capture_scene(scene, display))
    finally:
        _stop(xvfb)
    # Delete obsolete round-1 names only after every new capture succeeds.
    keep = {record[key] for record in records for key in ("png", "transcript")}
    for path in (*OUT.glob("*.png"), *OUT.glob("*.txt")):
        if path.name not in keep:
            path.unlink()
    captured = datetime.now().astimezone()
    manifest = {
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
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    _write_readme(records)
    print(f"Wrote {len(records)} real frames.")


if __name__ == "__main__":
    main()
