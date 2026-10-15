"""Availability belongs to a committed owner, including within one process."""

import sqlite3
import threading
from contextlib import contextmanager

from metamuse import jobs, store


def test_stale_same_pid_nonce_is_unavailable_until_new_commit(ctx, monkeypatch):
    first = jobs.Executor(ctx.store)
    first.__enter__()

    @contextmanager
    def busy():
        raise store._StorageBusy(sqlite3.SQLITE_BUSY)
        yield

    with monkeypatch.context() as patch:
        patch.setattr(ctx.store, "transaction", busy)
        first.__exit__()
    observer = store.Store(ctx.paths)
    assert not jobs.worker_available(observer)
    second = jobs.Executor(ctx.store)
    waiting, owned, finish = threading.Event(), threading.Event(), threading.Event()
    transaction = ctx.store.transaction
    errors = []

    @contextmanager
    def observed():
        waiting.set()
        with transaction() as conn:
            yield conn

    monkeypatch.setattr(ctx.store, "transaction", observed)

    def work():
        try:
            with second:
                owned.set()
                assert finish.wait(10)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=work)
    with sqlite3.connect(ctx.paths.database, isolation_level=None) as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        thread.start()
        try:
            assert waiting.wait(10)
            assert second.epoch is None
            assert not jobs.worker_available(observer), "The previous same-PID nonce is stale"
            blocker.rollback()
            assert owned.wait(10)
            assert second.nonce != first.nonce
            assert jobs.worker_available(observer)
        finally:
            blocker.rollback()
            second.request_stop()
            finish.set()
            thread.join(10)
    assert not thread.is_alive()
    assert not errors
    assert not jobs.worker_available(observer)
