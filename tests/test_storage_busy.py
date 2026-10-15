"""Contention classification, maintenance retry bounds, and claimed-step safety."""

import sqlite3
import threading
from contextlib import contextmanager

import pytest

from metamuse import cli, jobs, mcp, store
from metamuse import models as m


def sqlite_error(code, message="synthetic private SQL /path/value"):
    error = sqlite3.OperationalError(message)
    if code is not None:
        error.sqlite_errorcode = code
    return error


@pytest.mark.parametrize(
    "code",
    [
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_BUSY_SNAPSHOT,
        sqlite3.SQLITE_BUSY_RECOVERY,
        sqlite3.SQLITE_LOCKED,
        sqlite3.SQLITE_LOCKED_SHAREDCACHE,
    ],
)
@pytest.mark.parametrize("location", ["open", "statement"])
def test_busy_classified_by_extended_code(ctx, monkeypatch, code, location):
    error = sqlite_error(code, "synthetic unrelated diagnostic")
    if location == "open":

        def broken():
            raise error

        monkeypatch.setattr(ctx.store, "_open_database", broken)
    with pytest.raises(store._StorageBusy) as caught:
        with ctx.store.connection():
            raise error
    assert caught.value.sqlite_errorcode == code
    envelope = m.failure(caught.value).model_dump(mode="json")
    assert envelope["error"]["code"] == "E_STORAGE"
    assert envelope["error"]["retry_class"] == "never"
    assert "sqlite_errorcode" not in str(envelope)
    assert "synthetic unrelated" not in str(envelope)


@pytest.mark.parametrize(
    "code",
    [
        sqlite3.SQLITE_CORRUPT,
        sqlite3.SQLITE_IOERR,
        sqlite3.SQLITE_NOTADB,
        sqlite3.SQLITE_ERROR,
        None,
    ],
)
def test_busy_words_cannot_make_other_storage_errors_retryable(ctx, code):
    with pytest.raises(m.CoreError) as caught:
        with ctx.store.connection():
            raise sqlite_error(code, "database is locked SQLITE_BUSY SQLITE_LOCKED /secret")
    assert not isinstance(caught.value, store._StorageBusy)
    assert caught.value.detail.code == m.ErrorCode.STORAGE
    assert caught.value.detail.message == "SQLite operation failed"


def test_writer_timeout_matches_measurement_and_readers_do_not_lock(ctx, monkeypatch):
    assert store._BUSY_TIMEOUT_SECONDS == 30
    connect = sqlite3.connect
    timeouts = []

    def observed(*args, **kwargs):
        timeouts.append(kwargs.get("timeout"))
        return connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", observed)
    with connect(ctx.paths.database, isolation_level=None) as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        with ctx.store.connection() as conn:
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30_000
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
        blocker.rollback()
    assert timeouts == [30, 30]  # read-only version probe and writable open


def test_wal_transition_itself_can_be_busy(ctx, monkeypatch):
    monkeypatch.setattr(store, "_BUSY_TIMEOUT_SECONDS", 0.05)
    with sqlite3.connect(ctx.paths.database, isolation_level=None) as blocker:
        assert blocker.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
        blocker.execute("BEGIN IMMEDIATE")
        with pytest.raises(store._StorageBusy) as caught:
            with ctx.store.connection():
                pytest.fail("WAL transition needs the competing writer to release")
        assert caught.value.sqlite_errorcode & 0xFF == sqlite3.SQLITE_BUSY
        blocker.rollback()
    with ctx.store.connection() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_backoff_is_capped_jittered_and_counter_bounded(ctx, monkeypatch):
    executor = jobs.Executor(ctx.store)
    waits, ranges = [], []
    calls = 0

    def action():
        nonlocal calls
        calls += 1
        if calls > 20:  # even a mutant must finish
            return "unexpected"
        raise store._StorageBusy(sqlite3.SQLITE_BUSY)

    def uniform(low, high):
        ranges.append((low, high))
        return high

    def wait(seconds):
        waits.append(seconds)
        if len(waits) == 20:
            executor.request_stop()

    monkeypatch.setattr(jobs.random, "uniform", uniform)
    monkeypatch.setattr(executor._stop, "wait", wait)
    executor.storage_busy_retries = 2**31 - 2
    assert executor._retry_unclaimed(action) is None
    assert calls == 20
    assert waits[:3] == [0.05, 0.1, 0.2]
    assert max(waits) == 2
    assert all(low == high / 2 and low < high for low, high in ranges)
    assert executor.storage_busy_retries == 2**31 - 1


def test_stop_wakes_backoff_and_prevents_another_attempt(ctx, monkeypatch):
    executor = jobs.Executor(ctx.store)
    waiting = threading.Event()
    calls = []
    real_wait = executor._stop.wait

    def action():
        calls.append(True)
        if len(calls) > 1:
            return None
        raise store._StorageBusy(sqlite3.SQLITE_LOCKED)

    def wait(seconds):
        waiting.set()
        return real_wait(seconds)

    monkeypatch.setattr(jobs.random, "uniform", lambda low, high: 2.0)
    monkeypatch.setattr(executor._stop, "wait", wait)
    thread = threading.Thread(target=lambda: executor._retry_unclaimed(action))
    thread.start()
    try:
        assert waiting.wait(5)
        executor.request_stop()
        thread.join(1)
        assert not thread.is_alive()
        assert len(calls) == 1
    finally:
        executor.request_stop()
        thread.join(5)


def test_stop_during_attached_startup_busy_releases_worker_lock(ctx, monkeypatch):
    monkeypatch.setattr(store, "_BUSY_TIMEOUT_SECONDS", 0.05)
    attached = mcp._AttachedWorker(ctx, True)
    with sqlite3.connect(ctx.paths.database, isolation_level=None) as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        attached.thread.start()
        try:
            assert attached.ready.wait(30)  # first busy makes startup status observable
            assert attached.state == "starting"
            assert attached.status()["storage_busy_retries"] >= 1
            assert not attached.status()["worker_available"]
            attached.stop()
            attached.thread.join(5)
            assert not attached.thread.is_alive()
            assert attached.state == "stopped"
            assert attached.executor.handle is None
        finally:
            blocker.rollback()
            attached.stop()
            attached.thread.join(30)
    with jobs.Executor(ctx.store):
        pass


def test_busy_shutdown_metadata_cleanup_does_not_mask_lock_release(ctx, monkeypatch):
    monkeypatch.setattr(store, "_BUSY_TIMEOUT_SECONDS", 0.05)
    with sqlite3.connect(ctx.paths.database, isolation_level=None) as blocker:
        with jobs.Executor(ctx.store) as executor:
            epoch = executor.epoch
            executor.request_stop()
            blocker.execute("BEGIN IMMEDIATE")
        assert executor.handle is None
        assert not jobs.worker_available(ctx.store)
        blocker.rollback()
    with jobs.Executor(ctx.store) as recovered:
        assert recovered.epoch == epoch + 1


def test_requeued_safe_read_delay_retries_busy_heartbeat(ctx, monkeypatch):
    operations, job_id = operation(ctx, "safe_read", lambda step: m.JobResult())
    with jobs.Executor(ctx.store, registry=operations, sleep=lambda seconds: None) as executor:
        heartbeat = executor._heartbeat
        calls = []

        def once(conn):
            calls.append(True)
            if len(calls) == 1:
                raise store._StorageBusy(sqlite3.SQLITE_BUSY)
            heartbeat(conn)

        monkeypatch.setattr(executor, "_heartbeat", once)
        executor._retry_wait(job_id, 0.25)
        assert len(calls) == 2
        assert executor.storage_busy_retries == 1
        with ctx.store.connection() as conn:
            assert conn.execute("SELECT attempts FROM job_steps").fetchone()[0] == 0


@pytest.mark.parametrize("location", ["heartbeat", "cancel", "idle"])
def test_unclaimed_maintenance_busy_rolls_back_and_retries(ctx, monkeypatch, location):
    operations = {}
    jobs.register_operation("busy.fixture", lambda step: m.JobResult(), "none", registry=operations)
    job_id = jobs.submit_job(ctx.store, "busy.fixture", location, {}, registry=operations).data[
        "job_id"
    ]
    with ctx.store.transaction() as conn:
        conn.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))
    with jobs.Executor(ctx.store, registry=operations) as executor:
        name = {"heartbeat": "_heartbeat", "cancel": "_cancel", "idle": "unavailable_operations"}[
            location
        ]
        original = getattr(executor, name)
        calls = []

        def once(*args):
            calls.append(True)
            if len(calls) == 1:
                raise store._StorageBusy(sqlite3.SQLITE_BUSY)
            result = original(*args)
            if location == "idle":
                executor.request_stop()
            return result

        monkeypatch.setattr(executor, name, once)
        if location == "idle":
            executor.run()
        else:
            assert not executor.run_once()
        assert len(calls) == 2
        assert executor.storage_busy_retries == 1
        assert ctx.store.get(m.Job, job_id).state == "cancelled"


def operation(ctx, effect, handler):
    operations = {}
    jobs.register_operation(
        "busy.fixture", handler, effect, uncertain_state="send_uncertain", registry=operations
    )
    job_id = jobs.submit_job(ctx.store, "busy.fixture", "claimed", {}, registry=operations).data[
        "job_id"
    ]
    return operations, job_id


@pytest.mark.parametrize("effect", ["none", "safe_read", "external_submit"])
@pytest.mark.parametrize("after_boundary", [False, True])
def test_claimed_busy_follows_failure_policy_without_handler_replay(
    ctx, monkeypatch, effect, after_boundary
):
    calls = []

    def handler(step):
        calls.append(True)
        if after_boundary:
            step.before_external_action(step.target, verify=lambda conn, target: None)
        raise store._StorageBusy(sqlite3.SQLITE_BUSY_SNAPSHOT)

    operations, job_id = operation(ctx, effect, handler)
    monkeypatch.setattr(jobs.Executor, "require_network", lambda *a, **kw: None)
    with jobs.Executor(ctx.store, registry=operations) as executor:
        assert executor.run_once()
        assert not executor.run_once()
        assert executor.storage_busy_retries == 0
    job = ctx.store.get(m.Job, job_id)
    with ctx.store.connection() as conn:
        step = ctx.store.decode(m.JobStep, conn.execute("SELECT * FROM job_steps").fetchone())
    assert calls == [True]
    assert step.attempts == 1 and step.retries == 0
    if effect == "external_submit" and after_boundary:
        assert job.state == "awaiting_user"
        assert job.remote_state == "unknown"
        assert step.state == "send_uncertain"
    else:
        assert job.state == "failed"
        assert step.state == "failed"
        assert job.error["code"] == m.ErrorCode.STORAGE.value
        if effect == "external_submit":
            assert job.remote_state == "not_submitted"


@pytest.mark.parametrize("effect", ["none", "safe_read", "external_submit"])
@pytest.mark.parametrize("method", ["_fail", "_finish_step"])
@pytest.mark.parametrize("after_boundary", [False, True])
def test_busy_during_finalization_never_forges_result(
    ctx, monkeypatch, effect, method, after_boundary
):
    calls = []

    def handler(step):
        calls.append(True)
        if after_boundary:
            step.before_external_action(step.target, verify=lambda conn, target: None)
        if method == "_fail" or (effect == "external_submit" and not after_boundary):
            raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Synthetic failure")
        return m.JobResult()

    operations, job_id = operation(ctx, effect, handler)
    monkeypatch.setattr(jobs.Executor, "require_network", lambda *a, **kw: None)
    original = store.Store.transaction
    fail_writes = False

    @contextmanager
    def blocked(self):
        if fail_writes:
            raise store._StorageBusy(sqlite3.SQLITE_BUSY)
        with original(self) as conn:
            yield conn

    with jobs.Executor(ctx.store, registry=operations) as executor:
        # Inject after finalization writes, then keep the fallback writer busy too.
        # The current transaction must roll back every tentative result/error.
        finalizer = (
            "_uncertain"
            if effect == "external_submit" and after_boundary and method == "_fail"
            else "_fail"
            if effect == "external_submit" and not after_boundary
            else method
        )
        original_finalizer = getattr(executor, finalizer)

        def interrupted(*args):
            nonlocal fail_writes
            original_finalizer(*args)
            fail_writes = True
            raise store._StorageBusy(sqlite3.SQLITE_BUSY)

        monkeypatch.setattr(executor, finalizer, interrupted)
        monkeypatch.setattr(store.Store, "transaction", blocked)
        with pytest.raises(store._StorageBusy):
            executor.run(once=True)
        fail_writes = False
        assert executor.storage_busy_retries == 0
        job = ctx.store.get(m.Job, job_id)
        with ctx.store.connection() as conn:
            row = conn.execute("SELECT * FROM job_steps").fetchone()
            step = ctx.store.decode(m.JobStep, row)
        assert job.state == "running"
        assert step.state == (
            "submitting" if effect == "external_submit" and after_boundary else "running"
        )
        assert job.result is None and step.result is None
        assert job.remote_state != "not_submitted"
        assert calls == [True]
    # Recovery retains existing policy: entered external steps are always uncertain.
    if effect == "external_submit":
        with jobs.Executor(ctx.store, registry=operations):
            assert ctx.store.get(m.Job, job_id).state == "awaiting_user"
        assert calls == [True]


@pytest.mark.parametrize(
    "code", [m.ErrorCode.STORAGE, m.ErrorCode.PROTOCOL_MISMATCH, m.ErrorCode.JOB_BUSY, None]
)
@pytest.mark.parametrize("method", ["_start", "run"])
def test_attached_failure_diagnostics_are_code_only_in_doctor_and_mcp(
    ctx, monkeypatch, code, method
):
    def broken(executor):
        if code:
            raise m.CoreError(code, "synthetic private /path SQL content")
        raise RuntimeError("synthetic private /path SQL content")

    monkeypatch.setattr(jobs.Executor, method, broken)
    attached = mcp._AttachedWorker(ctx, True)
    ctx.worker_status = attached.status
    attached.thread.start()
    try:
        attached.thread.join(5)
        assert not attached.thread.is_alive()
        if method == "_start" and code == m.ErrorCode.JOB_BUSY:
            assert attached.status()["attached_worker"] == "lock_busy"
            return
        assert not hasattr(
            attached, "start_error"
        )  # the MCP server can continue serving diagnostics
        for result in [
            attached.status(),
            cli._dispatch(ctx, cli.parser().parse_args(["doctor"])).data,
            mcp.call_tool(ctx, "metamuse_inspect", {}).structured_content["data"],
        ]:
            assert result["attached_worker"] == "failed"
            assert result["worker_available"] is False
            assert result["worker_error_code"] == (code or m.ErrorCode.STORAGE).value
            assert "synthetic private" not in str(result)
    finally:
        attached.stop()
        attached.thread.join(10)
