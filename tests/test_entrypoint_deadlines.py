"""Finite, shared journey deadlines include CLI and MCP calls."""

import json
import subprocess
import time
import traceback
from types import SimpleNamespace

import anyio
import pytest
import test_journey
from test_journey import Entrypoints


def test_entrypoint_calls_share_remaining_test_deadline(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(test_journey, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    entry = Entrypoints(tmp_path)
    timeouts = []

    def run(command, **kw):
        timeouts.append(kw["timeout"])
        return subprocess.CompletedProcess(command, 0, json.dumps({"status": "ok", "data": {}}), "")

    monkeypatch.setattr(subprocess, "run", run)
    entry.cli("jobs", "list")
    clock[0] += 120
    entry.cli("worker", "run", "--once")
    assert 0 < timeouts[1] < timeouts[0]
    assert timeouts[0] - timeouts[1] == 120


def test_entrypoint_cli_hang_fails_with_command(tmp_path, monkeypatch):
    entry = Entrypoints(tmp_path)

    def hang(command, **kw):
        raise subprocess.TimeoutExpired(command, kw["timeout"])

    monkeypatch.setattr(subprocess, "run", hang)
    with pytest.raises(AssertionError, match="Test deadline exceeded: .*worker run --once"):
        entry.cli("worker", "run", "--once")


def test_entrypoint_mcp_hang_has_finite_shared_deadline(tmp_path):
    entry = Entrypoints(tmp_path)
    entry.deadline = time.monotonic() + 0.05

    class Hanging:
        async def call_tool(self, tool, request):
            await anyio.sleep_forever()

    async def scenario():
        with pytest.raises(AssertionError, match="Test deadline exceeded: MCP metamuse_job_get"):
            await entry.call(Hanging(), "job_get", {"job_id": "synthetic"})

    anyio.run(scenario)


def test_entrypoint_terminal_poll_deadline_names_command(tmp_path):
    entry = Entrypoints(tmp_path)
    entry.deadline = time.monotonic() + 0.02

    async def queued(*args, **kwargs):
        return {"data": {"job": {"state": "queued"}}}

    entry.call = queued

    async def scenario():
        with pytest.raises(
            AssertionError, match="Test deadline exceeded: MCP metamuse_job_get synthetic"
        ):
            await entry.terminal(None, "synthetic")

    anyio.run(scenario)


@pytest.mark.parametrize("hang", [False, True])
def test_entrypoint_real_mcp_hang_names_tool_and_disables_bytecode(tmp_path, hang):
    import sys

    entry = Entrypoints(tmp_path)
    server = tmp_path / "mcp-server"
    server.write_text(
        f"#!{sys.executable}\n"
        "import anyio, os\n"
        "from mcp.server.mcpserver import MCPServer\n"
        "app = MCPServer('deadline-fixture')\n"
        "@app.tool(name='metamuse_job_get')\n"
        "async def job_get(job_id: str) -> dict:\n"
        "    if job_id == 'hang':\n"
        "        await anyio.sleep_forever()\n"
        "    return {'bytecode': os.environ.get('PYTHONDONTWRITEBYTECODE')}\n"
        "app.run()\n"
    )
    server.chmod(0o755)
    entry.command = server
    entry.deadline = time.monotonic() + 20

    async def scenario():
        async with entry.server("--no-worker") as session:
            if hang:
                await entry.call(session, "job_get", {"job_id": "hang"})
            else:
                result = await session.call_tool("metamuse_job_get", {"job_id": "env"})
                assert json.loads(result.content[0].text) == {"bytecode": "1"}

    if hang:
        with pytest.raises(Exception) as held:
            anyio.run(scenario)
        detail = "".join(traceback.format_exception(held.value))
        assert "Test deadline exceeded: MCP metamuse_job_get" in detail
        assert "Test deadline exceeded: metamuse mcp" not in detail
    else:
        anyio.run(scenario)
