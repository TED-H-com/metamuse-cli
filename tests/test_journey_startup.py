"""Startup-only journey prerequisites; the complete journey is a separate gate."""

import json
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest
import test_journey
from test_journey import Entrypoints

from metamuse import jobs, mcp


def test_journey_waits_for_real_attached_worker_handoff(tmp_path, monkeypatch):
    _journey_with_startup_barrier(tmp_path, monkeypatch, "ownership")


def test_journey_waits_during_real_executor_construction(tmp_path, monkeypatch):
    _journey_with_startup_barrier(tmp_path, monkeypatch, "construction")


def _journey_with_startup_barrier(tmp_path, monkeypatch, phase):
    """Hold bootstrap across the unchanged 2s handoff, then release via inspect.

    Exercise the actual initial journey call site through the official SDK, stopping
    before project_create. Time starts before SDK spawn: construction, sibling Store
    creation and ownership all consume the same handoff, so time since _start is
    neither its clock nor guaranteed to exist when the first inspect returns.
    """
    entry = Entrypoints(tmp_path)
    deadline = entry.deadline
    started = tmp_path / "start"
    release = tmp_path / "release"
    wrapper = tmp_path / "gated-metamuse"
    wrapper.write_text(
        f"#!{sys.executable}\n"
        "import socket, sys, time\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(Path(jobs.__file__).parents[1])!r})\n"
        "from metamuse import jobs\n"
        "from metamuse.cli import main\n"
        "def forbidden(*args, **kwargs):\n"
        "    raise AssertionError('Network forbidden in startup fixture')\n"
        "socket.socket.connect = socket.socket.connect_ex = forbidden\n"
        "socket.create_connection = forbidden\n"
        "def await_release():\n"
        f"    Path({str(started)!r}).touch()\n"
        f"    while not Path({str(release)!r}).exists():\n"
        f"        assert time.monotonic() < {deadline!r}, 'Startup gate exceeded shared deadline'\n"
        "        time.sleep(0.01)\n"
        "original = jobs.Executor._start\n"
        "def gated(self):\n"
        "    await_release()\n"
        "    return original(self)\n"
        f"if {phase!r} == 'construction':\n"
        "    construction_original = jobs.Executor.__init__\n"
        "    def gated_init(self, *args, **kwargs):\n"
        "        await_release()\n"
        "        return construction_original(self, *args, **kwargs)\n"
        "    jobs.Executor.__init__ = gated_init\n"
        "else:\n"
        "    jobs.Executor._start = gated\n"
        "raise SystemExit(main())\n"
    )
    wrapper.chmod(0o755)
    entry.command = wrapper
    observed = []
    spawn_started = None
    original_call, original_server = entry.call, entry.server

    class FlowReached(Exception):
        pass

    async def call(session, tool, request, **kwargs):
        if tool == "project_create":
            # No journey operations are allowed before genuine worker readiness.
            assert observed[-1]["worker_available"] is True, observed
            assert observed[-1]["attached_worker"] == "running", observed
            raise FlowReached
        result = await original_call(session, tool, request, **kwargs)
        if tool == "inspect":
            data = result["data"]
            observed.append(data)
            if data["attached_worker"] == "starting":
                assert data["worker_available"] is False, data
                assert spawn_started is not None
                assert time.monotonic() - spawn_started >= mcp._WORKER_START_HANDOFF_SECONDS
                # A slow bootstrap may not have reached this phase at initial
                # inspect. Wait for the gate explicitly within the same deadline.
                with entry.waiting("Startup fixture phase entry"):
                    while not started.exists():
                        await anyio.sleep(0.01)
                # One immediate inspect must still be insufficient. Release only
                # after the readiness helper has explicitly polled a second time.
                if len(observed) == 2:
                    release.touch()
        return result

    @asynccontextmanager
    async def server(*args, **kwargs):
        nonlocal spawn_started
        spawn_started = time.monotonic()
        async with original_server(*args, **kwargs) as session:
            try:
                yield session
            finally:
                # Release before SDK teardown, including on the old assertion.
                release.touch()

    entry.call, entry.server = call, server
    monkeypatch.setattr(test_journey, "Entrypoints", lambda path: entry)

    def leaves(exc):
        if isinstance(exc, BaseExceptionGroup):
            return [leaf for child in exc.exceptions for leaf in leaves(child)]
        return [exc]

    with pytest.raises(BaseExceptionGroup) as held:
        test_journey.test_mcp_only_journey_followup_restart_and_export(tmp_path)
    if not all(isinstance(exc, FlowReached) for exc in leaves(held.value)):
        raise held.value
    assert [data["attached_worker"] for data in observed[:2]] == ["starting", "starting"]
    assert entry.deadline == deadline


@pytest.mark.parametrize(
    ("state", "available"),
    [
        ("failed", False),
        ("disabled", False),
        ("lock_busy", False),
        ("stopped", False),
        ("running", False),
        ("starting", True),
    ],
)
def test_worker_readiness_rejects_nontransitional_state(tmp_path, state, available):
    entry = Entrypoints(tmp_path)
    worker = {
        "attached_worker": state,
        "worker_available": available,
        "worker_error_code": "E_STORAGE",
        "worker_next_action": "Synthetic next action",
        "extra_diagnostics": {"retained": [1, 2]},
    }
    calls = []

    async def inspect(session, tool, request):
        assert (tool, request) == ("inspect", {})
        calls.append(tool)
        return {"data": worker}

    entry.call = inspect

    async def scenario():
        with pytest.raises(AssertionError, match="Attached worker did not become ready") as held:
            await entry.wait_worker_ready(None)
        assert json.dumps(worker, ensure_ascii=False) in str(held.value)

    anyio.run(scenario)
    assert calls == ["inspect"]
    assert entry.last_worker == worker


def test_worker_readiness_keeps_shared_deadline_and_last_full_state(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(test_journey, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    entry = Entrypoints(tmp_path)
    deadline = entry.deadline
    # Earlier CLI/MCP work already spent 599 seconds of the existing 600.
    clock[0] = 599.0
    worker = {
        "attached_worker": "starting",
        "worker_available": False,
        "worker_error_code": None,
        "worker_next_action": "Synthetic waiting action",
        "extra_diagnostics": {"retained": [3, 4]},
    }
    observed_budgets = []
    calls = []

    def fail_after(seconds):
        observed_budgets.append(seconds)
        return anyio.fail_after(seconds)

    async def poll_interval(seconds):
        assert seconds == 0.05
        clock[0] += 1.0

    monkeypatch.setattr(
        test_journey, "anyio", SimpleNamespace(fail_after=fail_after, sleep=poll_interval)
    )

    async def inspect(session, tool, request):
        entry.remaining("MCP metamuse_inspect")
        calls.append(tool)
        return {"data": worker}

    entry.call = inspect

    async def scenario():
        with pytest.raises(
            AssertionError, match="Test deadline exceeded: MCP metamuse_inspect"
        ) as held:
            await entry.wait_worker_ready(None)
        assert json.dumps(worker, ensure_ascii=False) in str(held.value)

    anyio.run(scenario)
    assert calls == ["inspect"]
    assert observed_budgets == [1.0]
    assert entry.deadline == deadline == 600.0
