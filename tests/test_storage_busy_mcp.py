"""MCP serves while its independent worker Store waits for a writer."""

import json
import os
import selectors
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

from metamuse import jobs, mcp, store
from metamuse import models as m


def wait_for(predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "Handshake timed out"
        time.sleep(0.01)


def test_attached_constructs_independent_store_in_worker_thread(ctx, monkeypatch):
    original = store.Store.__init__
    constructors = []

    def observed(self, *args, **kwargs):
        constructors.append(threading.get_ident())
        original(self, *args, **kwargs)

    monkeypatch.setattr(store.Store, "__init__", observed)
    attached = mcp._AttachedWorker(ctx, True)
    attached.thread.start()
    try:
        assert attached.ready.wait(10)
        assert attached.state == "running"
        worker_store = attached.executor.store
        assert worker_store is not ctx.store
        assert worker_store.paths == ctx.store.paths
        assert worker_store.clock is ctx.store.clock
        assert worker_store.schema_version == ctx.store.schema_version
        assert worker_store._migrations == ctx.store._migrations
        assert constructors == [attached.thread.ident]
    finally:
        attached.stop()
        attached.thread.join(10)
    assert not attached.thread.is_alive()


SCRIPT = r"""
import socket, sys, time
from pathlib import Path
from metamuse import app, jobs, mcp
from metamuse import models as m
def forbidden(*args, **kwargs):
    raise AssertionError("Network forbidden")
socket.socket.connect = socket.socket.connect_ex = forbidden
socket.create_connection = forbidden
workspace, hook, phase = sys.argv[1:]
hook = Path(hook)
ctx = app.AppContext.open(workspace)
jobs._UNCLAIMED_BUSY_TIMEOUT_SECONDS = 0.2
mcp._WORKER_START_HANDOFF_SECONDS = 0.3
jobs.register_operation("busy.fixture", lambda step: m.JobResult(), "none")
def await_file(name):
    deadline = time.monotonic() + 15
    while not (hook / name).exists():
        assert time.monotonic() < deadline, name
        time.sleep(0.01)
original = jobs.Executor._claim
def claim(self):
    if not (hook / "claim").exists():
        (hook / "claim").touch()
        await_file("claim-go")
    return original(self)
jobs.Executor._claim = claim
if phase == "handoff":
    start = jobs.Executor._start
    def gated_start(self):
        (hook / "start").touch()
        await_file("start-go")
        return start(self)
    jobs.Executor._start = gated_start
(hook / "opened").touch()
await_file("go")
mcp.run(ctx)
"""


class Client:
    """Small JSON-RPC wire probe; shutdown remains under this test's control."""

    def __init__(self, child):
        self.child = child
        self.buffer = b""
        self.sequence = 0
        self.selector = selectors.DefaultSelector()
        self.selector.register(child.stdout, selectors.EVENT_READ)

    def send(self, message):
        self.child.stdin.write(json.dumps({"jsonrpc": "2.0", **message}).encode() + b"\n")
        self.child.stdin.flush()

    def request(self, method, params, timeout=2):
        self.sequence += 1
        self.send({"id": self.sequence, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                response = json.loads(line)
                if response.get("id") == self.sequence:
                    assert "error" not in response, response
                    return response["result"]
            remaining = deadline - time.monotonic()
            assert remaining > 0 and self.selector.select(remaining), f"{method} timed out"
            chunk = os.read(self.child.stdout.fileno(), 65536)
            assert chunk, f"MCP exited: {self.child.poll()}"
            self.buffer += chunk

    def tool(self, name, arguments=None, timeout=2):
        result = self.request("tools/call", {"name": name, "arguments": arguments or {}}, timeout)
        return result["structuredContent"]


def run_server(ctx, tmp_path, phase):
    hook = tmp_path / "hook"
    hook.mkdir()
    errors = (tmp_path / "server.log").open("w+")
    child = subprocess.Popen(
        [sys.executable, "-c", SCRIPT, str(ctx.paths.root), str(hook), phase],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=errors,
        env=dict(os.environ, PYTHONPATH=str(Path(store.__file__).parents[1])),
    )
    return child, Client(child), hook, errors


def close_server(child, client, hook, errors):
    # Unblock every injected gate even if an assertion failed or a mutant hung startup.
    for name in ("go", "start-go", "claim-go"):
        (hook / name).touch()
    client.selector.close()
    if child.poll() is None:
        child.stdin.close()
        try:
            child.wait(10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(10)
    child.stdout.close()
    errors.close()


def initialize(client):
    client.request(
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "contention-fixture", "version": "1"},
        },
    )
    client.send({"method": "notifications/initialized"})


def test_mcp_startup_and_job_reads_serve_during_writer_wait(ctx, tmp_path):
    operations = {}
    jobs.register_operation("busy.fixture", lambda step: m.JobResult(), "none", registry=operations)
    job_id = jobs.submit_job(ctx.store, "busy.fixture", "mcp", {}, registry=operations).data[
        "job_id"
    ]
    child, client, hook, errors = run_server(ctx, tmp_path, "busy")
    with sqlite3.connect(ctx.paths.database, isolation_level=None) as blocker:
        try:
            wait_for(lambda: (hook / "opened").exists())
            blocker.execute("BEGIN IMMEDIATE")
            (hook / "go").touch()
            started = time.monotonic()
            initialize(client)
            status = client.tool("metamuse_inspect")["data"]
            assert time.monotonic() - started < 2
            assert status["attached_worker"] == "starting"
            assert not status["worker_available"]
            assert status["storage_busy_retries"] > 0
            blocker.rollback()
            wait_for(lambda: (hook / "claim").exists())
            status = client.tool("metamuse_inspect")["data"]
            assert status["attached_worker"] == "running" and status["worker_available"]
            blocker.execute("BEGIN IMMEDIATE")
            (hook / "claim-go").touch()
            # Ensure an actual _claim busy observation before timing the read.
            retries = status["storage_busy_retries"]
            wait_for(
                lambda: client.tool("metamuse_inspect")["data"]["storage_busy_retries"] > retries
            )
            started = time.monotonic()
            job = client.tool("metamuse_job_get", {"job_id": job_id}, timeout=1.5)["data"]
            assert time.monotonic() - started < 1.5
            assert job["job"]["state"] == "queued" and job["steps"][0]["attempts"] == 0
            blocker.rollback()
            wait_for(
                lambda: (
                    client.tool("metamuse_job_get", {"job_id": job_id})["data"]["job"]["state"]
                    == "succeeded"
                )
            )
        finally:
            blocker.rollback()
            close_server(child, client, hook, errors)
    assert child.returncode == 0


def test_mcp_handoff_serves_before_worker_ready(ctx, tmp_path):
    child, client, hook, errors = run_server(ctx, tmp_path, "handoff")
    try:
        wait_for(lambda: (hook / "opened").exists())
        (hook / "go").touch()
        wait_for(lambda: (hook / "start").exists())
        initialize(client)
        status = client.tool("metamuse_inspect")["data"]
        assert status["attached_worker"] == "starting" and not status["worker_available"]
        (hook / "start-go").touch()
        (hook / "claim-go").touch()
        wait_for(lambda: client.tool("metamuse_inspect")["data"]["attached_worker"] == "running")
    finally:
        close_server(child, client, hook, errors)
    assert child.returncode == 0
