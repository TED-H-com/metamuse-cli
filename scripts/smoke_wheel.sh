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
# Every migration and generated schema in the source tree must ship byte-identical.
data = root / "src" / "metamuse" / "data"
sources = sorted([*data.glob("migrations/*.sql"), *data.glob("schemas/*.json")])
if not any(path.parent.name == "migrations" for path in sources):
    raise SystemExit("source tree has no migrations")
required = {
    f"metamuse/data/{path.relative_to(data).as_posix()}": path.read_bytes() for path in sources
}
with zipfile.ZipFile(wheel) as archive:
    names = set(archive.namelist())
    if "metamuse/data/THIRD_PARTY_NOTICES.md" not in names:
        raise SystemExit("wheel is missing metamuse/data/THIRD_PARTY_NOTICES.md")
    missing = sorted(name for name in required if name not in names)
    if missing:
        raise SystemExit(f"wheel is missing {missing}")
    changed = sorted(name for name, body in required.items() if archive.read(name) != body)
    if changed:
        raise SystemExit(f"wheel data differs from the source tree: {changed}")
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

env -u PYTHONPATH "$bin" --workspace "$work/demo" --json demo >"$work/demo.json"
env -u PYTHONPATH "$py" - "$work/demo.json" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

envelope = json.loads(Path(sys.argv[1]).read_text())
assert envelope["status"] == "ok"
demo = envelope["data"]
assert demo["synthetic_data"] and demo["prewritten_recap"]
assert demo["network_mode"] == "offline"
assert demo["imported_records"] == 3 and demo["search_hits"] == 1
directory = Path(demo["export_directory"])
manifest = json.loads((directory / "manifest.json").read_text())
markdown = "\n".join(path.read_text() for path in directory.glob("*.md"))
assert "預寫示例，非 AI 分析" in markdown
assert "https://example.org/events/demo/comments/1" in markdown
for member in manifest["files"]:
    assert hashlib.sha256((directory / member["name"]).read_bytes()).hexdigest() == member["sha256"]
print("installed demo ok: packaged synthetic input, cited Markdown, verified manifest hashes")
PY

env -u PYTHONPATH "$py" - "$bin" "$work" <<'PY'
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

binary, directory = sys.argv[1:]
root = Path(directory)
token = root / "synthetic-page-token.txt"
token.write_text("SYNTHETIC_WHEEL_PAGE_TOKEN")
token.chmod(0o600)
workspace = root / "connect-ws"
result = subprocess.run(
    [binary, "--workspace", str(workspace), "--json", "connect", "publishing",
     "--account", "synthetic-page", "--auth-file", str(token),
     "--page", "123456", "--api-version", "v25.0"],
    env={**os.environ, "XDG_CONFIG_HOME": str(root / "config")},
    capture_output=True, text=True, timeout=30, check=True,
)
body = json.loads(result.stdout)["data"]
policy = tomllib.loads((workspace / "metamuse.toml").read_text())
assert body["configured"] is True and body["account_verified"] is False
assert policy["app"]["network_mode"] == "offline"
assert policy["publishing"]["auth_ref"] == body["auth_ref"]
assert "SYNTHETIC_WHEEL_PAGE_TOKEN" not in result.stdout + result.stderr
assert "SYNTHETIC_WHEEL_PAGE_TOKEN" not in (workspace / "metamuse.toml").read_text()
print("installed account setup ok: offline, private binding, no remote verification claim")
PY

env -u PYTHONPATH "$py" - "$bin" "$work/ws" "$work" "$ROOT/src/metamuse/data/schemas/mcp-tools.json" <<'PY'
import asyncio
import json
import os
import sys

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

binary, workspace, work, contract = sys.argv[1:]
env = {
    "PYTHONUNBUFFERED": "1",
    "PATH": os.environ.get("PATH", ""),
    "HOME": os.environ.get("HOME", ""),
    "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
}
# The generated contract is the source of truth for the tool list.
with open(contract, encoding="utf-8") as handle:
    expected = {tool["name"] for tool in json.load(handle)["tools"]}
if not expected:
    raise SystemExit("generated MCP contract lists no tools")


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
    print(f"mcp stdio ok: {len(expected)} tools, metamuse_inspect")


asyncio.run(main())
PY

echo "wheel smoke passed"
