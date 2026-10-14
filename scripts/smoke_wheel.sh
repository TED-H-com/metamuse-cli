#!/usr/bin/env bash
# Build a wheel, install it outside the checkout, and smoke the CLI and MCP stdio.
# Requires a warm uv cache from `uv sync --locked` (builds and installs with --offline).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
base="${TMPDIR:-/tmp}"
work="$(mktemp -d "${base}/metamuse-wheel-smoke.XXXXXX")"
trap 'rm -rf "$work"' EXIT

(cd "$ROOT" && uv build --wheel --offline --out-dir "$work/dist")
mapfile -t wheels < <(find "$work/dist" -maxdepth 1 -type f -name '*.whl' -print)
if [[ ${#wheels[@]} -ne 1 ]]; then
  echo "expected one wheel, found ${#wheels[@]}" >&2
  exit 1
fi
wheel="${wheels[0]}"

uv venv --python 3.12 "$work/venv"
# Install locked runtime dependencies with the same offline cache `uv sync` used,
# then install only the built wheel. `uv pip install` of the wheel re-resolves
# registry versions and misses that cache when the network is disabled.
VIRTUAL_ENV="$work/venv" uv sync \
  --directory "$ROOT" \
  --python "$work/venv/bin/python" \
  --frozen \
  --offline \
  --no-dev \
  --no-install-project \
  --active
VIRTUAL_ENV="$work/venv" uv pip install \
  --python "$work/venv/bin/python" \
  --offline \
  --no-deps \
  "$wheel"

bin="$work/venv/bin/metamuse"
py="$work/venv/bin/python"
cd "$work"

env -u PYTHONPATH "$py" - "$ROOT" "$wheel" "$work" <<'PY'
import os
import sys
import zipfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
wheel = Path(sys.argv[2])
work = Path(sys.argv[3]).resolve()

import metamuse

origin = Path(metamuse.__file__).resolve()
prefix = Path(sys.prefix).resolve()
if not origin.is_relative_to(prefix):
    raise SystemExit(f"import metamuse resolved outside the venv: {origin}")
if origin.is_relative_to(root) or root in origin.parents:
    raise SystemExit(f"import metamuse resolved inside the checkout: {origin}")
if origin.is_relative_to(work):
    pass

canonical = root / "skills" / "metamuse"
expected: dict[str, bytes] = {}
for path in canonical.rglob("*"):
    if not path.is_file():
        continue
    if "__pycache__" in path.parts or path.suffix == ".pyc":
        continue
    expected[path.relative_to(canonical).as_posix()] = path.read_bytes()
if "SKILL.md" not in expected:
    raise SystemExit("canonical Skill is missing SKILL.md")

prefix_skill = "metamuse/data/skills/metamuse/"
required = [
    "metamuse/data/THIRD_PARTY_NOTICES.md",
    "metamuse/data/migrations/001_initial.sql",
    "metamuse/data/migrations/002_collect_media.sql",
    "metamuse/data/migrations/003_muse_lifecycle.sql",
    "metamuse/data/migrations/004_muse_evidence.sql",
    "metamuse/data/schemas/envelope.json",
    "metamuse/data/schemas/mcp-tools.json",
]
with zipfile.ZipFile(wheel) as archive:
    names = set(archive.namelist())
    missing = [name for name in required if name not in names]
    if missing:
        raise SystemExit(f"wheel is missing {missing}")
    packaged = {
        name[len(prefix_skill) :]: archive.read(name)
        for name in names
        if name.startswith(prefix_skill) and not name.endswith("/")
    }
if packaged != expected:
    raise SystemExit(
        "wheel Skill differs from skills/metamuse: "
        f"only wheel={sorted(set(packaged) - set(expected))} "
        f"only source={sorted(set(expected) - set(packaged))}"
    )
print(f"wheel ok: {wheel.name}; import={origin}")
PY

env -u PYTHONPATH "$bin" --help >/dev/null
env -u PYTHONPATH "$bin" --workspace "$work/ws" --json init >"$work/init.json"
env -u PYTHONPATH "$bin" --workspace "$work/ws" --json doctor >"$work/doctor.json"
env -u PYTHONPATH "$py" - "$work/init.json" "$work/doctor.json" <<'PY'
import json
import sys
from pathlib import Path

for name in sys.argv[1:]:
    envelope = json.loads(Path(name).read_text(encoding="utf-8"))
    if envelope.get("status") != "ok":
        raise SystemExit(f"{name} status={envelope.get('status')}")
print("init and doctor ok")
PY

env -u PYTHONPATH "$py" - "$bin" "$work/ws" "$work" <<'PY'
import asyncio
import os
import sys

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

binary, workspace, work = sys.argv[1:]
env = {
    "PYTHONUNBUFFERED": "1",
    "PATH": os.environ.get("PATH", ""),
    "HOME": os.environ.get("HOME", ""),
    "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
}
expected = {
    "metamuse_collect",
    "metamuse_draft_preview",
    "metamuse_draft_save",
    "metamuse_export",
    "metamuse_inspect",
    "metamuse_job_control",
    "metamuse_job_get",
    "metamuse_job_list",
    "metamuse_muse_close_exchange",
    "metamuse_muse_open",
    "metamuse_muse_read",
    "metamuse_muse_send",
    "metamuse_packet_prepare",
    "metamuse_project_create",
    "metamuse_search",
}


async def main() -> None:
    params = StdioServerParameters(
        command=binary,
        args=["--workspace", workspace, "--non-interactive", "mcp"],
        env=env,
        cwd=work,
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            init = await session.initialize()
            if init.server_info.name != "metamuse":
                raise SystemExit(f"unexpected server {init.server_info.name}")
            listed = await session.list_tools()
            names = {tool.name for tool in listed.tools}
            if names != expected:
                raise SystemExit(f"tool list mismatch: {sorted(names)}")
            result = await session.call_tool("metamuse_inspect", {})
            if result.is_error:
                raise SystemExit("metamuse_inspect returned an error")
            body = result.structured_content
            if not isinstance(body, dict) or body.get("status") != "ok":
                raise SystemExit(f"metamuse_inspect status={getattr(body, 'get', lambda *_: None)('status')}")
            text = result.content[0].text
            if '"status":"ok"' not in text.replace(" ", ""):
                raise SystemExit("metamuse_inspect text content is not an ok envelope")
    print("mcp stdio ok: 15 tools, metamuse_inspect")


asyncio.run(main())
PY

echo "wheel smoke passed"
