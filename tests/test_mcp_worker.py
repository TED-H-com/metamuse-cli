"""Real MCP subprocess lock/shutdown/recovery tests, synthetic fault injection only."""

import json
import os
import signal
import subprocess
import threading
import time

import anyio
import pytest
from test_journey import Entrypoints, mock


def test_lock_contention_and_no_worker_do_not_claim_execution(tmp_path):
    entry = Entrypoints(tmp_path)
    entry.configure()
    log = tmp_path / "owner.log"
    with log.open("w+") as errlog:
        owner = subprocess.Popen(
            [str(entry.command), "--workspace", str(entry.workspace), "worker", "run"],
            stdin=subprocess.DEVNULL,
            stdout=errlog,
            stderr=errlog,
        )
        try:
            deadline = time.monotonic() + 30
            while not entry.cli("doctor")["data"]["worker_available"]:
                assert owner.poll() is None and time.monotonic() < deadline
                time.sleep(0.05)

            async def scenario():
                async with entry.server() as session:
                    inspected = (await entry.call(session, "inspect", {}))["data"]
                    assert inspected["worker_available"] is False
                    assert inspected["attached_worker"] == "lock_busy"
                    assert "does not retry" in inspected["worker_next_action"]
                    # A live owner remains untouched; this server never takes over it.
                    assert owner.poll() is None
                    owner.terminate()
                    owner.wait(timeout=10)
                    assert entry.cli("doctor")["data"]["worker_available"] is False
                    assert (await entry.call(session, "inspect", {}))["data"][
                        "worker_available"
                    ] is False
                    project = (
                        await entry.call(
                            session,
                            "project_create",
                            {"name": "Contention", "request_key": "project"},
                        )
                    )["data"]["id"]
                    accepted = await entry.call(
                        session,
                        "collect",
                        {
                            "project_id": project,
                            "inputs": [{"kind": "text", "text": "Synthetic queued text"}],
                            "resources": ["post"],
                            "limits": {
                                "max_urls": 10,
                                "max_items": 500,
                                "max_bytes": 1024,
                                "wall_clock_seconds": 60,
                            },
                            "access_grant_id": None,
                            "request_key": "collect",
                        },
                    )
                    assert accepted["data"]["worker_available"] is False
                    job_id = accepted["data"]["job_id"]
                    queued = (await entry.call(session, "job_get", {"job_id": job_id}))["data"]
                    assert (
                        queued["job"]["state"] == "queued" and queued["steps"][0]["attempts"] == 0
                    )
                async with entry.server("--no-worker") as session:
                    inspected = (await entry.call(session, "inspect", {}))["data"]
                    assert (
                        inspected["attached_worker"] == "disabled"
                        and inspected["worker_available"] is False
                    )
                    assert (await entry.call(session, "job_get", {"job_id": job_id}))[
                        "data"
                    ] == queued
                async with entry.server() as session:
                    final = await entry.terminal(session, job_id)
                    assert final["steps"][0]["attempts"] == 1

            anyio.run(scenario)
        finally:
            if owner.poll() is None:
                owner.terminate()
                owner.wait(timeout=10)
    assert entry.cli("doctor")["data"]["worker_available"] is False


@pytest.mark.parametrize("stop", ["EOF", "SIGTERM", "SIGINT", "SIGKILL"])
def test_mcp_mid_send_shutdown_or_crash_recovers_without_resend(tmp_path, stop):
    entry = Entrypoints(tmp_path)
    entry.configure()
    hook = tmp_path / "hook"
    hook.mkdir()
    entered = tmp_path / "entered.json"
    release = tmp_path / "release"
    sends = tmp_path / "sends.txt"
    storage_errors = tmp_path / "storage-errors.txt"
    # The hook pauses after the existing durable handoff checkpoint but before job completion.
    # This is a fault test; the user journey test has no injection or internal access.
    (hook / "sitecustomize.py").write_text(
        "import json, os, sqlite3, time\nfrom pathlib import Path\n"
        "from metamuse import _muse_lifecycle as lifecycle\n"
        "from metamuse._muse_fixture import OfflineMuseGateway\n"
        f"entered = Path({str(entered)!r})\n"
        f"release = Path({str(release)!r})\n"
        f"sends = Path({str(sends)!r})\n"
        "from contextlib import contextmanager\nfrom metamuse.store import Store\n"
        f"storage_errors = Path({str(storage_errors)!r})\n"
        "original_connection = Store.connection\n"
        "@contextmanager\n"
        "def connection(self):\n"
        "    try:\n"
        "        with original_connection(self) as conn: yield conn\n"
        "    except Exception as exc:\n"
        "        cause = exc.__context__\n"
        "        if isinstance(cause, sqlite3.Error):\n"
        "            with storage_errors.open('a') as handle:\n"
        "                handle.write(cause.sqlite_errorname + ': ' + str(cause) + '\\n')\n"
        "        raise\n"
        "Store.connection = connection\n"
        "original_send = OfflineMuseGateway.chat_stream\n"
        "def send(self, prepared, **kwargs):\n"
        "    with sends.open('a') as handle: handle.write(prepared.marker + '\\n')\n"
        "    return original_send(self, prepared, **kwargs)\n"
        "OfflineMuseGateway.chat_stream = send\n"
        "original_save = lifecycle._save_send\n"
        "def save(step, discussion, exchange, result, failure):\n"
        "    original_save(step, discussion, exchange, result, failure)\n"
        "    entered.write_text(json.dumps({'pid': os.getpid(), 'job_id': step.job.id}))\n"
        "    deadline = time.monotonic() + 45\n"
        "    while not release.exists() and time.monotonic() < deadline: time.sleep(.02)\n"
        "lifecycle._save_send = save\n"
    )
    env = {"PYTHONPATH": str(hook) + os.pathsep + os.environ.get("PYTHONPATH", "")}

    async def scenario():
        async with entry.server(env=env) as session:
            project = (
                await entry.call(
                    session, "project_create", {"name": "Shutdown", "request_key": "project"}
                )
            )["data"]["id"]
            collected = (
                await entry.call(
                    session,
                    "collect",
                    {
                        "project_id": project,
                        "inputs": [{"kind": "text", "text": "Synthetic boundary evidence"}],
                        "resources": ["post"],
                        "limits": {
                            "max_urls": 10,
                            "max_items": 500,
                            "max_bytes": 1024,
                            "wall_clock_seconds": 60,
                        },
                        "access_grant_id": None,
                        "request_key": "collect",
                    },
                )
            )["data"]
            await entry.terminal(session, collected["job_id"])
            refs = [
                r["ref"]
                for r in (
                    await entry.call(
                        session, "search", {"project_id": project, "query": "Synthetic"}
                    )
                )["data"]["results"]
            ]
            packet = (
                await entry.call(
                    session,
                    "packet_prepare",
                    {"project_id": project, "selection": refs, "request_key": "packet"},
                )
            )["data"]
            opened = (
                await entry.approved_call(
                    session,
                    "muse.open",
                    {"project_id": project, "topic": "Shutdown fixture", "request_key": "open"},
                    "approve-open",
                )
            )["data"]
            await entry.terminal(session, opened["job_id"])
            sent = (
                await entry.approved_call(
                    session,
                    "muse.send",
                    {
                        "discussion_id": opened["discussion_id"],
                        "packet_id": packet["id"],
                        "question": "Show boundary evidence.",
                        "request_key": "send",
                    },
                    "approve-send",
                )
            )["data"]
            with anyio.fail_after(30):
                while not entered.exists():
                    await anyio.sleep(0.02)
            state = (await entry.call(session, "job_get", {"job_id": sent["job_id"]}))["data"]
            assert state["job"]["state"] == "running" and state["steps"][0]["state"] == "submitting"
            queued = (
                await entry.call(
                    session,
                    "collect",
                    {
                        "project_id": project,
                        "inputs": [
                            {"kind": "text", "text": "Synthetic next step waits for restart"}
                        ],
                        "resources": ["post"],
                        "limits": {
                            "max_urls": 10,
                            "max_items": 500,
                            "max_bytes": 1024,
                            "wall_clock_seconds": 60,
                        },
                        "access_grant_id": None,
                        "request_key": "after-shutdown",
                    },
                )
            )["data"]
            pid = json.loads(entered.read_text())["pid"]
            if stop == "EOF":
                # Official stdio_client closes stdin on context exit; release the paused
                # step shortly afterward so EOF teardown must drain it before unlocking.
                timer = threading.Timer(0.2, release.touch)
                timer.start()
            else:
                os.kill(pid, getattr(signal, stop))
            if stop not in ("SIGKILL", "EOF"):
                await anyio.sleep(0.1)
                # Graceful shutdown still owns the lock while the bounded step drains.
                assert entry.cli("doctor")["data"]["worker_available"] is True
                entry.cli("worker", "run", "--once", code=5)
                release.touch()
            # Official client closes the pipe and joins the real process on context exit.
        assert entry.cli("doctor")["data"]["worker_available"] is False
        waiting = entry.cli("jobs", "get", queued["job_id"])["data"]
        assert waiting["job"]["state"] == "queued" and waiting["steps"][0]["attempts"] == 0
        release.touch()
        async with entry.server(env=env) as session:
            await entry.terminal(session, queued["job_id"])
            state = (await entry.call(session, "job_get", {"job_id": sent["job_id"]}))["data"]
            if stop == "SIGKILL":
                assert state["job"]["state"] == "awaiting_user"
                assert state["steps"][0]["state"] == "send_uncertain"
                denied = await entry.call(
                    session,
                    "job_control",
                    {
                        "job_id": sent["job_id"],
                        "operation": "resume",
                        "request_key": "unsafe-resume",
                    },
                    error=True,
                )
                assert denied["error"]["code"] == "E_EXTERNAL_STATE_UNKNOWN"
            else:
                assert state["job"]["state"] == "succeeded"
            refreshed = (
                await entry.approved_call(
                    session,
                    "muse.read",
                    {"exchange_id": sent["exchange_id"], "refresh": True},
                    "approve-recovery",
                )
            )["data"]
            await entry.terminal(session, refreshed["refresh_job_id"])
            settled = await entry.terminal(session, sent["job_id"])
            assert settled["steps"][0]["attempts"] == 1
            read = (await entry.call(session, "muse_read", {"exchange_id": sent["exchange_id"]}))[
                "data"
            ]
            mock(read)
            assert read["observations"] and read["association"] == "progress"
            assert read["exchange"]["bytes_handed_off"] is True
        assert len(sends.read_text().splitlines()) == 1
        assert entry.cli("doctor")["data"]["worker_available"] is False

    try:
        anyio.run(scenario)
    finally:
        if storage_errors.exists():
            print(storage_errors.read_text())
    entry.hygiene()
