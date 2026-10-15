"""Post-loop and idle queries obey unclaimed retry and stop semantics."""

import json
import sqlite3
from contextlib import contextmanager

import pytest

from metamuse import cli, jobs, store
from metamuse import models as m


def test_post_run_unavailable_query_retries_busy(ctx, monkeypatch, capsys):
    operations = {}
    jobs.register_operation("busy.fixture", lambda step: m.JobResult(), "none", registry=operations)
    monkeypatch.setattr(jobs, "OPERATIONS", operations)
    job_id = jobs.submit_job(ctx.store, "busy.fixture", "once", {}, registry=operations).data[
        "job_id"
    ]
    run, connection = jobs.Executor.run, store.Store.connection
    post_run = False
    failures = []

    def finished(self, **kwargs):
        nonlocal post_run
        count = run(self, **kwargs)
        post_run = True
        return count

    @contextmanager
    def query(self):
        nonlocal post_run
        if post_run:
            post_run = False
            failures.append(True)
            raise store._StorageBusy(sqlite3.SQLITE_BUSY)
        with connection(self) as conn:
            yield conn

    monkeypatch.setattr(jobs.Executor, "run", finished)
    monkeypatch.setattr(store.Store, "connection", query)
    assert cli.main(["--workspace", str(ctx.paths.root), "--json", "worker", "run", "--once"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ok"
    assert result["data"]["steps_processed"] == 1
    assert failures == [True]
    assert ctx.store.get(m.Job, job_id).state == "succeeded"


def test_stopped_unavailable_query_does_not_read(ctx, monkeypatch):
    executor = jobs.Executor(ctx.store)
    executor.request_stop()

    def forbidden():
        pytest.fail("A stopped Executor must not open another connection")

    monkeypatch.setattr(ctx.store, "connection", forbidden)
    assert executor.unavailable_operations() is False
