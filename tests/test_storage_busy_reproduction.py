"""Real SQLite locks, with handshakes instead of disk stalls or timing guesses."""

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from metamuse import jobs, mcp, store
from metamuse import models as m


def wait_for(predicate, seconds=30):
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "Worker handshake did not complete"
        time.sleep(0.01)


@pytest.mark.parametrize("phase", ["startup", "claim"])
def test_attached_worker_survives_write_lock(ctx, monkeypatch, phase):
    monkeypatch.setattr(store, "_BUSY_TIMEOUT_SECONDS", 0.05)
    reached, proceed, busy = threading.Event(), threading.Event(), threading.Event()
    attached = mcp._AttachedWorker(ctx, True)
    operations = {}

    def handler(step):
        step.executor.request_stop()
        return m.JobResult(coverage_status="scope_satisfied")

    jobs.register_operation("busy.fixture", handler, "none", registry=operations)
    monkeypatch.setattr(jobs, "OPERATIONS", operations)
    job_id = jobs.submit_job(ctx.store, "busy.fixture", phase, {}, registry=operations).data[
        "job_id"
    ]
    method = "__enter__" if phase == "startup" else "_claim"
    original = getattr(jobs.Executor, method)

    def gate(executor):
        reached.set()
        assert proceed.wait(30)
        return original(executor)

    monkeypatch.setattr(jobs.Executor, method, gate)
    connection = store.Store.connection

    @contextmanager
    def observed(self):
        try:
            with connection(self) as conn:
                yield conn
        except m.CoreError as exc:
            if exc.detail.code == m.ErrorCode.STORAGE:
                busy.set()
            raise

    monkeypatch.setattr(store.Store, "connection", observed)
    attached.thread.start()
    try:
        assert reached.wait(30)
        with sqlite3.connect(ctx.paths.database, isolation_level=None) as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            proceed.set()
            assert busy.wait(30), "Lock must outlast SQLite busy_timeout"
            wait_for(
                lambda: (
                    attached.state == "failed"
                    or attached.status().get("storage_busy_retries", 0) > 0
                )
            )
            assert attached.state != "failed", attached.status()
            assert attached.status()["storage_busy_retries"] >= 1
            assert attached.thread.is_alive()
            blocker.rollback()
        attached.thread.join(30)
        assert not attached.thread.is_alive()
        assert attached.state == "stopped", attached.status()
        assert ctx.store.get(m.Job, job_id).state == "succeeded"
        assert ctx.store.get(m.Job, job_id).remote_state == "unknown"
    finally:
        proceed.set()
        attached.stop()
        attached.thread.join(30)


FOREGROUND = r"""
import socket
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from metamuse import cli, jobs, store
from metamuse import models as m

def forbidden(*args, **kwargs):
    raise AssertionError("Network forbidden")
socket.socket.connect = socket.socket.connect_ex = forbidden
socket.create_connection = forbidden
workspace, handshake, phase = sys.argv[1:]
handshake = Path(handshake)
store._BUSY_TIMEOUT_SECONDS = 0.05
method = "__enter__" if phase == "startup" else "_claim"
original = getattr(jobs.Executor, method)
def gate(executor):
    (handshake / "ready").touch()
    deadline = time.monotonic() + 30
    while not (handshake / "go").exists():
        assert time.monotonic() < deadline
        time.sleep(0.01)
    return original(executor)
setattr(jobs.Executor, method, gate)
connection = store.Store.connection
@contextmanager
def observed(self):
    try:
        with connection(self) as conn:
            yield conn
    except m.CoreError as exc:
        if exc.detail.code == m.ErrorCode.STORAGE:
            (handshake / "busy").touch()
        raise
store.Store.connection = observed
jobs.register_operation("busy.fixture", lambda step: m.JobResult(), "none")
raise SystemExit(cli.main(["--workspace", workspace, "--json", "worker", "run", "--once"]))
"""


@pytest.mark.parametrize("phase", ["startup", "claim"])
def test_foreground_worker_survives_write_lock(ctx, tmp_path, phase):
    operations = {}
    jobs.register_operation("busy.fixture", lambda step: m.JobResult(), "none", registry=operations)
    job_id = jobs.submit_job(ctx.store, "busy.fixture", phase, {}, registry=operations).data[
        "job_id"
    ]
    handshake = tmp_path / "handshake"
    handshake.mkdir()
    env = dict(os.environ, PYTHONPATH=str(Path(store.__file__).parents[1]))
    child = subprocess.Popen(
        [sys.executable, "-c", FOREGROUND, str(ctx.paths.root), str(handshake), phase],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        wait_for(lambda: (handshake / "ready").exists() or child.poll() is not None)
        assert child.poll() is None
        with sqlite3.connect(ctx.paths.database, isolation_level=None) as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            (handshake / "go").touch()
            wait_for(lambda: (handshake / "busy").exists() or child.poll() is not None)
            assert (handshake / "busy").exists(), "Lock must outlast SQLite busy_timeout"
            blocker.rollback()
        stdout, stderr = child.communicate(timeout=30)
        assert child.returncode == 0, (stdout, stderr)
        assert json.loads(stdout)["data"]["steps_processed"] == 1
        assert ctx.store.get(m.Job, job_id).state == "succeeded"
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=30)


def test_mcp_serves_sanitized_diagnostics_after_worker_startup_failure(ctx, tmp_path):
    script = r"""
import socket
import sys
from metamuse import cli, jobs
from metamuse import models as m
def forbidden(*args, **kwargs):
    raise AssertionError("Network forbidden")
socket.socket.connect = socket.socket.connect_ex = forbidden
socket.create_connection = forbidden
def broken(executor):
    raise m.CoreError(m.ErrorCode.STORAGE, "synthetic private /path SQL content")
jobs.Executor._start = broken
raise SystemExit(cli.main(["--workspace", sys.argv[1], "--json", "mcp"]))
"""

    async def scenario():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-c", script, str(ctx.paths.root)],
            env={"PYTHONPATH": str(Path(store.__file__).parents[1])},
        )
        with (tmp_path / "stderr.log").open("w+") as errors:
            with anyio.fail_after(60):
                async with stdio_client(params, errlog=errors) as streams:
                    async with ClientSession(*streams) as session:
                        await session.initialize()
                        result = await session.call_tool("metamuse_inspect", {})
                        data = result.structured_content["data"]
                        assert data["attached_worker"] == "failed"
                        assert data["worker_error_code"] == "E_STORAGE"
                        assert not data["worker_available"]
                        assert "synthetic private" not in str(result)
            errors.seek(0)
            assert "synthetic private" not in errors.read()

    anyio.run(scenario)
