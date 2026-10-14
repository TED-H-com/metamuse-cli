"""Finite, shared journey deadlines include CLI and MCP calls."""

import json
import subprocess
import time
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
