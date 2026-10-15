"""Real lock waits bound unclaimed stop and OS-lock release latency."""

import fcntl
import sqlite3
import threading
import time
from contextlib import contextmanager

import pytest

from metamuse import jobs, store
from metamuse import models as m


@pytest.mark.parametrize("phase", ["start", "claim", "retry_wait", "cleanup"])
def test_stop_during_sqlite_wait_is_short(ctx, monkeypatch, phase, record_property):
    assert store._BUSY_TIMEOUT_SECONDS == 30
    short = 0.2
    monkeypatch.setattr(jobs, "_UNCLAIMED_BUSY_TIMEOUT_SECONDS", short, raising=False)
    operations = {}
    jobs.register_operation("busy.fixture", lambda step: m.JobResult(), "none", registry=operations)
    job_id = jobs.submit_job(ctx.store, "busy.fixture", "wait", {}, registry=operations).data[
        "job_id"
    ]
    executor = jobs.Executor(ctx.store, registry=operations)
    owned, proceed, waiting = threading.Event(), threading.Event(), threading.Event()
    errors = []
    active = phase == "start"
    transaction = ctx.store.transaction

    @contextmanager
    def observed():
        if active:
            waiting.set()
        with transaction() as conn:
            yield conn

    monkeypatch.setattr(ctx.store, "transaction", observed)

    def work():
        nonlocal active
        try:
            with executor:
                owned.set()
                assert proceed.wait(10)
                active = True
                if phase == "claim":
                    executor.run_once()
                elif phase == "retry_wait":
                    executor._retry_wait(job_id, 60)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=work)
    with sqlite3.connect(ctx.paths.database, isolation_level=None) as blocker:
        try:
            if phase == "start":
                blocker.execute("BEGIN IMMEDIATE")
            thread.start()
            if phase != "start":
                assert owned.wait(10)
                blocker.execute("BEGIN IMMEDIATE")
            proceed.set()
            assert waiting.wait(10)
            # The transaction has entered its actual SQLite busy wait.
            time.sleep(0.03)
            started = time.monotonic()
            executor.request_stop()
            thread.join(2 * short + 1)
            elapsed = time.monotonic() - started
            assert not thread.is_alive(), f"{phase} stop exceeded {2 * short + 1}s"
            with ctx.paths.worker_lock.open("r+b") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                released = time.monotonic() - started
                fcntl.flock(handle, fcntl.LOCK_UN)
            record_property("stop_seconds", elapsed)
            record_property("lock_release_seconds", released)
            assert released <= 2 * short + 1
            assert not errors
        finally:
            executor.request_stop()
            proceed.set()
            blocker.rollback()
            if thread.ident is not None:
                thread.join(10)
    assert not thread.is_alive()


def test_exit_propagates_nonbusy_error_after_releasing_lock(ctx, monkeypatch):
    executor = jobs.Executor(ctx.store)
    executor.__enter__()

    @contextmanager
    def broken():
        raise m.CoreError(m.ErrorCode.PROTOCOL_MISMATCH, "synthetic internal failure")
        yield

    with monkeypatch.context() as patch:
        patch.setattr(ctx.store, "transaction", broken)
        with pytest.raises(m.CoreError, match="synthetic internal failure"):
            executor.__exit__()
    assert executor.handle is None
    with ctx.paths.worker_lock.open("r+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(handle, fcntl.LOCK_UN)


def test_unclaimed_timeout_is_scoped_and_thread_local(ctx, monkeypatch):
    assert store._BUSY_TIMEOUT_SECONDS == 30
    monkeypatch.setattr(jobs, "_UNCLAIMED_BUSY_TIMEOUT_SECONDS", 0.2, raising=False)
    executor = jobs.Executor(ctx.store)
    connect = sqlite3.connect
    timeouts = []

    def observed(*args, **kwargs):
        timeouts.append(kwargs.get("timeout"))
        return connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", observed)

    def read():
        with ctx.store.connection() as conn:
            return conn.execute("PRAGMA busy_timeout").fetchone()[0]

    def action():
        assert read() == 200
        # A second thread must retain the default even while this override is active.
        values = []
        thread = threading.Thread(target=lambda: values.append(read()))
        thread.start()
        try:
            thread.join(2)
            assert not thread.is_alive()
            assert values == [30_000]
        finally:
            thread.join(10)
        return True

    assert executor._retry_unclaimed(action)
    assert read() == 30_000
    assert timeouts == [0.2, 0.2, 30, 30, 30, 30]

    def broken():
        assert read() == 200
        raise ValueError("synthetic")

    with pytest.raises(ValueError):
        executor._retry_unclaimed(broken)
    assert read() == 30_000
