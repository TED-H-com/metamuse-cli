"""Fixed storage advice from SQLite codes, without copying SQL or credentials."""

import sqlite3
from contextlib import contextmanager

import pytest
from test_muse_standard import Connecting, assignment, response, rv
from test_muse_transport import FakeHttp, build, ok_frames
from test_muse_trust import freeze

from metamuse.models import CoreError, ErrorCode


@pytest.mark.parametrize(
    "code,word,retry",
    [
        (sqlite3.SQLITE_BUSY, "busy; retry", "safe_read"),
        (sqlite3.SQLITE_LOCKED, "busy; retry", "safe_read"),
        (sqlite3.SQLITE_READONLY, "read-only", "user_action"),
        (sqlite3.SQLITE_FULL, "disk is full", "user_action"),
    ],
)
@pytest.mark.parametrize("path", ["freeze", "transport", "label_busy", "rv", "hint"])
def test_r4_2_storage_codes_are_honest(ctx, monkeypatch, code, word, retry, path):
    original = ctx.store.transaction
    failing = {"on": path not in {"label_busy", "rv"}}

    @contextmanager
    def transaction():
        if failing["on"]:
            error = sqlite3.OperationalError("private SQL sentinel must not escape")
            error.sqlite_errorcode = code
            # The real Store.connection translates SQLite errors into a CoreError.
            try:
                raise error
            except sqlite3.Error:
                raise CoreError(ErrorCode.STORAGE, "SQLite operation failed") from None
        with original() as conn:
            yield conn

    monkeypatch.setattr(ctx.store, "transaction", transaction)
    if path == "label_busy":
        record = ctx.store._muse_record_observation

        def record_then_fail(*args, **kwargs):
            record(*args, **kwargs)
            failing["on"] = True

        monkeypatch.setattr(ctx.store, "_muse_record_observation", record_then_fail)
    connector = (
        Connecting(transform=lambda key, nonce: rv(), before_reply=lambda: failing.update(on=True))
        if path == "rv"
        else Connecting()
    )
    transport, *_ = build(
        handler=ok_frames([]),
        store=ctx.store,
        connector=connector,
        http=FakeHttp({"/api/session": response(assignment(cvm_required=True))})
        if path == "hint"
        else None,
    )
    with pytest.raises(CoreError) as caught:
        if path in {"freeze", "label_busy"}:
            freeze(ctx.store, confidential=path == "label_busy")
        else:
            transport.sessions_list()
    detail = caught.value.detail
    assert detail.code == ErrorCode.STORAGE
    assert detail.retry_class == retry
    if path in {"hint", "rv"}:
        assert detail.message == "Muse confidentiality could not be recorded"
        assert (
            "writer"
            if code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
            else "writable"
            if code == sqlite3.SQLITE_READONLY
            else "disk"
        ) in detail.next_action
    else:
        assert word in detail.message
    assert "restore" not in detail.next_action.lower()
    assert "sentinel" not in str(detail)
    assert all(peer.msg3_payload is None for peer in connector.peers)
    if path == "label_busy":
        with ctx.store.connection() as conn:
            assert (
                conn.execute(
                    "SELECT confidential_required FROM muse_trust_observations"
                ).fetchone()[0]
                == 1
            )


def test_r4_2_real_busy_and_readonly(ctx, monkeypatch):
    import metamuse.store as core

    original = core._configure
    state = {"readonly": False}

    def configure(conn, **kwargs):
        original(conn, **kwargs)
        conn.execute("PRAGMA busy_timeout=10")
        if state["readonly"]:
            conn.execute("PRAGMA query_only=1")

    monkeypatch.setattr(core, "_configure", configure)
    with ctx.store._muse_boundary("synthetic", "account"):
        pass
    raw = sqlite3.connect(ctx.paths.database, isolation_level=None)
    try:
        raw.execute("BEGIN IMMEDIATE")
        with pytest.raises(CoreError) as caught:
            freeze(ctx.store)
        assert caught.value.detail.message == "Muse security state is busy; retry"
    finally:
        raw.close()
    state["readonly"] = True
    with pytest.raises(CoreError) as caught:
        freeze(ctx.store)
    assert "read-only" in caught.value.detail.message
