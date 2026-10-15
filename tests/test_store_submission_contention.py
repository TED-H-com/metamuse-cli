"""A timed-out acceptance does not create work or misreport worker ownership."""

import sqlite3
from contextlib import closing

from metamuse import jobs
from metamuse import models as m


def test_busy_acceptance_preserves_queue_idempotency_and_ownership(ctx):
    operations, calls = {}, []

    def handler(step):
        calls.append(step.job.id)
        return m.JobResult()

    jobs.register_operation("contention.fixture", handler, "none", registry=operations)

    def submit():
        return jobs.submit_job(
            ctx.store, "contention.fixture", "same-request", {}, registry=operations
        )

    def counts():
        with ctx.store.connection() as conn:
            return tuple(
                conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("jobs", "job_steps", "events")
            )

    assert not jobs.worker_available(ctx.store)
    with jobs.Executor(ctx.store, registry=operations) as executor:
        before = counts()
        with closing(sqlite3.connect(ctx.paths.database, isolation_level=None)) as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            try:
                with ctx.store._busy_timeout(0.05):
                    result = submit()
                assert result.status == "error" and result.data is None
                error = result.error
                assert error.code == m.ErrorCode.STORAGE
                assert error.retry_class == "never"
                assert counts() == before
                assert jobs.worker_available(ctx.store)
                assert not calls
            finally:
                blocker.rollback()
        accepted = submit()
        job_id = accepted.data["job_id"]
        assert accepted.status == "accepted"
        assert accepted.data["state"] == "queued"
        assert accepted.data["worker_available"]
        replay = submit()
        assert replay.data["job_id"] == job_id
        assert executor.run_once()
        assert ctx.store.get(m.Job, job_id).state == "succeeded"
        assert calls == [job_id]
        assert not executor.run_once()
    assert not jobs.worker_available(ctx.store)
