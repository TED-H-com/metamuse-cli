"""Cancellation and double-busy boundary regressions, including mutant cleanup."""

import sqlite3
import threading
import time
from contextlib import contextmanager

import pytest

from metamuse import jobs, store
from metamuse import models as m


def submit(ctx, effect, handler):
    operations = {}
    jobs.register_operation(
        "busy.fixture", handler, effect, uncertain_state="send_uncertain", registry=operations
    )
    job_id = jobs.submit_job(ctx.store, "busy.fixture", "boundary", {}, registry=operations).data[
        "job_id"
    ]
    return operations, job_id


@pytest.mark.parametrize("effect", ["none", "safe_read", "external_submit"])
def test_double_busy_escapes_claimed_execution_without_replay(ctx, monkeypatch, effect):
    calls = []
    armed, blocked = False, []
    transaction = ctx.store.transaction

    def handler(step):
        nonlocal armed
        calls.append(True)
        if effect == "external_submit":
            step.before_external_action(step.target, verify=lambda conn, target: None)
        armed = True
        return m.JobResult()

    @contextmanager
    def busy():
        # Fail finalization AND fallback. A wrong retry wrapper must finish rather
        # than hang: subsequent calls work, exposing replay or forged completion.
        if armed and len(blocked) < 2:
            blocked.append(True)
            raise store._StorageBusy(sqlite3.SQLITE_BUSY)
        with transaction() as conn:
            yield conn

    operations, job_id = submit(ctx, effect, handler)
    monkeypatch.setattr(jobs.Executor, "require_network", lambda *args, **kwargs: None)
    with jobs.Executor(ctx.store, registry=operations) as executor:
        with monkeypatch.context() as patch:
            patch.setattr(ctx.store, "transaction", busy)
            with pytest.raises(store._StorageBusy):
                executor.run_once()
        assert executor.storage_busy_retries == 0
        assert calls == [True] and blocked == [True, True]
        assert ctx.store.get(m.Job, job_id).state == "running"
        with ctx.store.connection() as conn:
            step = ctx.store.decode(m.JobStep, conn.execute("SELECT * FROM job_steps").fetchone())
        assert step.state == ("submitting" if effect == "external_submit" else "running")
        assert step.attempts == 1 and step.result is None
    with jobs.Executor(ctx.store, registry=operations):
        recovered = ctx.store.get(m.Job, job_id)
        with ctx.store.connection() as conn:
            step = ctx.store.decode(m.JobStep, conn.execute("SELECT * FROM job_steps").fetchone())
        if effect == "external_submit":
            assert recovered.state == "awaiting_user" and recovered.remote_state == "unknown"
            assert step.state == "send_uncertain"
        else:
            assert step.state == "queued"
    assert calls == [True]


def test_retry_wait_cancellation_stops_maintenance_once(ctx, monkeypatch):
    operations, job_id = submit(ctx, "safe_read", lambda step: m.JobResult())
    with ctx.store.transaction() as conn:
        conn.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))
    with jobs.Executor(ctx.store, registry=operations, sleep=lambda seconds: None) as executor:
        calls = []
        heartbeat = executor._heartbeat

        def observed(conn):
            calls.append(True)
            heartbeat(conn)

        monkeypatch.setattr(executor, "_heartbeat", observed)
        executor._retry_wait(job_id, 0.75)
        assert calls == [True]
    assert ctx.store.get(m.Job, job_id).state == "cancelled"
    with ctx.store.connection() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM events WHERE job_id=? AND kind='job.cancelled'", (job_id,)
            ).fetchone()[0]
            == 1
        )


def test_quiet_retry_wait_stop_does_not_start_another_attempt(ctx, monkeypatch, record_property):
    operations, job_id = submit(ctx, "safe_read", lambda step: m.JobResult())
    sleeping = threading.Event()

    def sleep(seconds):
        sleeping.set()
        time.sleep(seconds)

    with jobs.Executor(ctx.store, registry=operations, sleep=sleep) as executor:
        attempts = []
        retry = executor._retry_unclaimed

        def observed(action):
            attempts.append(True)
            return retry(action)

        monkeypatch.setattr(executor, "_retry_unclaimed", observed)
        thread = threading.Thread(target=lambda: executor._retry_wait(job_id, 60))
        thread.start()
        try:
            assert sleeping.wait(10)
            started = time.monotonic()
            executor.request_stop()
            thread.join(0.5)
            assert not thread.is_alive()
            elapsed = time.monotonic() - started
            record_property("quiet_stop_seconds", elapsed)
            assert elapsed <= 0.5
            assert attempts == [True]
        finally:
            executor.request_stop()
            thread.join(10)
