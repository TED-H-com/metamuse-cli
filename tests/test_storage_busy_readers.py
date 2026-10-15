"""Existing workspaces open without taking SQLite's write lock."""

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from metamuse import app, cli, jobs, store
from metamuse import models as m
from metamuse.paths import WorkspacePaths


@pytest.mark.parametrize("command", ["doctor", "get", "list", "search"])
def test_read_command_does_not_provision_under_write_lock(ctx, capsys, command):
    project = app.create_project(ctx, {"name": "fixture", "request_key": "project"})
    project_id = project.data["id"]
    operations = {}
    jobs.register_operation("busy.fixture", lambda step: m.JobResult(), "none", registry=operations)
    job_id = jobs.submit_job(ctx.store, "busy.fixture", "read", {}, registry=operations).data[
        "job_id"
    ]
    args = {
        "doctor": ["doctor"],
        "get": ["jobs", "get", job_id],
        "list": ["jobs", "list"],
        "search": ["search", "--project", project_id, "--query", "fixture"],
    }[command]
    results, errors = [], []

    def read():
        try:
            results.append(cli.main(["--workspace", str(ctx.paths.root), "--json", *args]))
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=read)
    with sqlite3.connect(ctx.paths.database, isolation_level=None) as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        thread.start()
        try:
            thread.join(2)
            assert not thread.is_alive(), "Read command waited for the write lock"
            assert time.monotonic() - started < 2
            assert not errors
            assert results == [0], capsys.readouterr()
        finally:
            blocker.rollback()
            thread.join(10)
    assert not thread.is_alive()


def test_two_first_opens_provision_exactly_one_active_key(tmp_path, monkeypatch):
    paths = WorkspacePaths.resolve(tmp_path / "first-opens")
    barrier = threading.Barrier(2)
    original = store.Store._provision_workspace_keys

    def provision(self):
        barrier.wait(10)
        original(self)

    monkeypatch.setattr(store.Store, "_provision_workspace_keys", provision)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = [
            future.result(15)
            for future in [pool.submit(store.Store, paths), pool.submit(store.Store, paths)]
        ]
    assert first.get_workspace_key(store.CONTENT_CURSOR_KEY) == second.get_workspace_key(
        store.CONTENT_CURSOR_KEY
    )
    with first.connection() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM workspace_keys WHERE name=? AND status='active'",
                (store.CONTENT_CURSOR_KEY,),
            ).fetchone()[0]
            == 1
        )
