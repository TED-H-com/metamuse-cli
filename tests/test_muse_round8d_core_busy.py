"""Real contention, sibling invalidation and claimed Muse work on canonical v5."""

import hashlib
import sqlite3
import time
from contextlib import closing, contextmanager
from functools import wraps
from types import SimpleNamespace

import pytest
from test_muse_round8_notifications import freeze
from test_muse_standard import Connecting, assignment, response, rv
from test_muse_transport import Credentials, FakeHttp, Guard, build, ok_frames

from metamuse import _muse_lifecycle as lifecycle
from metamuse import jobs
from metamuse import models as m
from metamuse import store as core
from metamuse.paths import WorkspacePaths

BUSY = "Muse security state is busy; retry"
RECORD = "Muse confidentiality could not be recorded"


@contextmanager
def writer_lock(store):
    with closing(sqlite3.connect(store.paths.database, isolation_level=None)) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        finally:
            conn.rollback()


def assert_busy(error, store, *, recording=False):
    assert type(error) is m.CoreError
    assert not isinstance(error, core._StorageBusy)
    assert error.detail.code == m.ErrorCode.STORAGE
    assert error.detail.message == (RECORD if recording else BUSY)
    assert error.detail.retry_class == "safe_read"
    assert error.detail.next_action == "Retry the connection after the workspace writer finishes"
    assert error.__cause__ is error.__context__ is None
    public = str(m.failure(error).model_dump(mode="json"))
    for private in (
        "_StorageBusy",
        "SQLite write lock is busy",
        "BEGIN",
        "SELECT",
        str(store.paths.root),
    ):
        assert private not in public


@pytest.mark.parametrize(
    "code",
    [
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_BUSY_RECOVERY,
        sqlite3.SQLITE_BUSY_SNAPSHOT,
        sqlite3.SQLITE_LOCKED,
        sqlite3.SQLITE_LOCKED_SHAREDCACHE,
    ],
)
def test_muse_maps_core_busy_marker_without_a_cause(ctx, code):
    marker = core._StorageBusy(code)
    assert marker.__cause__ is marker.__context__ is None
    assert_busy(core._muse_fixed_storage(marker), ctx.store)


@pytest.mark.parametrize("operation", ["freeze", "hint", "rv", "current", "handoff"])
def test_real_wal_busy_uses_scoped_timeout_and_muse_classification(ctx, operation, record_property):
    snapshot = freeze(ctx.store)
    handed = []
    with writer_lock(ctx.store), ctx.store._busy_timeout(0.25):
        # Both open/version probe and transaction use the scoped core constant.
        with ctx.store.connection() as conn:
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 250
        started = time.monotonic()
        with pytest.raises(m.CoreError) as caught:
            if operation == "freeze":
                freeze(ctx.store, session_binding_id="rotated")
            elif operation == "hint":
                freeze(ctx.store, confidential=True)
            elif operation == "rv":
                ctx.store.muse_record_confidential(snapshot)
            elif operation == "current":
                ctx.store.muse_assert_current(snapshot)
            else:
                ctx.store.muse_standard_handoff(snapshot, lambda: handed.append("msg3"))
        elapsed = time.monotonic() - started
    assert elapsed < 15, f"scoped 0.25s wait took {elapsed:.3f}s (default is 30s)"
    record_property("busy_wait_seconds", elapsed)
    print(f"busy {operation}: {elapsed:.6f}s")
    assert_busy(caught.value, ctx.store, recording=operation in {"hint", "rv"})
    assert not handed
    ctx.store.muse_assert_current(snapshot)
    with ctx.store.connection() as conn:
        assert not conn.execute(
            "SELECT 1 FROM muse_trust_observations WHERE confidential_required=1"
        ).fetchone()


def test_wal_schema_and_barrier_reads_do_not_need_the_writer(ctx):
    freeze(ctx.store)
    with writer_lock(ctx.store), ctx.store._busy_timeout(0.25):
        # This boundary only reads schema/barrier state. In contrast, assert_current
        # above deliberately opens BEGIN IMMEDIATE for its currentness transaction.
        with ctx.store._muse_boundary("synthetic", "a"):
            pass


def test_open_transport_busy_recheck_precedes_any_request(ctx):
    connector = Connecting()
    transport, http, *_ = build(handler=ok_frames([]), store=ctx.store, connector=connector)
    with transport:
        transport.sessions_list()
        requests = len(http.calls)
        with writer_lock(ctx.store), ctx.store._busy_timeout(0.25):
            started = time.monotonic()
            with pytest.raises(m.CoreError) as caught:
                transport.sessions_list()
            assert time.monotonic() - started < 15
        assert_busy(caught.value, ctx.store)
        assert len(http.calls) == requests and len(connector.urls) == 1
        assert connector.peers[0].closed and len(connector.peers[0].frames) == 1


@pytest.mark.parametrize("mode", ["standard", "hint", "rv"])
def test_busy_transport_never_submits_or_leaks_core_marker(ctx, mode, record_property):
    blocker = sqlite3.connect(ctx.paths.database, isolation_level=None)
    try:

        def lock():
            blocker.execute("BEGIN IMMEDIATE")

        connector = (
            Connecting(transform=lambda key, nonce: rv(), before_reply=lock)
            if mode == "rv"
            else Connecting()
        )
        http = (
            FakeHttp({"/api/session": response(assignment(cvm_required=True))})
            if mode == "hint"
            else FakeHttp()
        )
        transport, *_ = build(
            handler=ok_frames([]), store=ctx.store, connector=connector, http=http
        )
        if mode != "rv":
            lock()
        with transport, ctx.store._busy_timeout(0.25):
            started = time.monotonic()
            with pytest.raises(m.CoreError) as caught:
                transport.sessions_list()
            elapsed = time.monotonic() - started
        assert elapsed < 15
        print(f"busy transport {mode}: {elapsed:.6f}s")
        record_property("busy_wait_seconds", elapsed)
        assert_busy(caught.value, ctx.store, recording=mode != "standard")
        assert all(not peer.frames and peer.msg3_payload is None for peer in connector.peers)
        assert len(connector.urls) == (mode == "rv")
    finally:
        blocker.close()


def siblings(ctx, reverse):
    sibling = ctx.store._sibling()
    assert sibling.paths == ctx.store.paths
    assert sibling._connections is not ctx.store._connections
    assert sibling.schema_version == core.SCHEMA_VERSION
    assert sibling._migrations == core.MIGRATIONS and 5 in sibling._migrations
    return (sibling, ctx.store) if reverse else (ctx.store, sibling)


@pytest.mark.parametrize("reverse", [False, True], ids=["original_listener", "sibling_listener"])
@pytest.mark.parametrize("kind", ["label", "confidential"])
def test_sibling_writes_close_registered_transport_in_both_directions(ctx, reverse, kind):
    reader, writer = siblings(ctx, reverse)
    connector = Connecting()
    transport, http, *_ = build(handler=ok_frames([]), store=reader, connector=connector)
    with transport:
        transport.sessions_list()
        requests = len(http.calls)
        if kind == "label":
            writer.muse_freeze(
                provider="synthetic",
                account="synthetic",
                vm_id="vm1",
                viewer_id="viewer-fixture",
                session_binding_id="rotated",
            )
        else:
            freeze(writer, confidential=True)
        assert transport._trust_invalidated and connector.peers[0].closed
        if kind == "label":
            with pytest.raises(m.CoreError) as caught:
                transport.sessions_list()
            assert caught.value.detail.message == "Muse connection context changed"
            assert len(http.calls) == requests and len(connector.urls) == 1
        else:
            assert transport.sessions_list().status == 200
            assert len(connector.urls) == 2
        assert all(len(peer.frames) == 1 for peer in connector.peers)


@pytest.mark.parametrize("reverse", [False, True], ids=["original_listener", "sibling_listener"])
@pytest.mark.parametrize("phase", ["mint", "final"])
def test_sibling_write_respects_early_registration_and_final_check(
    ctx, monkeypatch, reverse, phase
):
    reader, writer = siblings(ctx, reverse)
    connector = Connecting()
    transport, *_ = build(handler=ok_frames([]), store=reader, connector=connector)
    fired = False

    def write_once():
        nonlocal fired
        if not fired:
            fired = True
            freeze(writer, confidential=True)

    if phase == "mint":
        original = transport._mint

        def before_mint(*args):
            write_once()
            return original(*args)

        monkeypatch.setattr(transport, "_mint", before_mint)
    else:
        original = reader.muse_assert_current

        def before_final(snapshot):
            if transport._loop is not None:
                write_once()
            return original(snapshot)

        monkeypatch.setattr(reader, "muse_assert_current", before_final)
    with transport:
        assert transport.sessions_list().status == 200
        assert fired and len(connector.urls) == 2
        assert connector.peers[0].closed
        assert [len(peer.frames) for peer in connector.peers] == [0, 1]


def file_hashes(directory):
    return {
        str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in directory.rglob("*")
        if p.is_file()
    }


def test_busy_v4_to_canonical_v5_retains_data_and_pre_upgrade_backup(tmp_path, monkeypatch):
    paths = WorkspacePaths.resolve(tmp_path / "upgrade")
    target_migrations = {v: core.MIGRATIONS[v] for v in range(1, 6)}
    old = core.Store(
        paths, schema_version=4, migrations={v: core.MIGRATIONS[v] for v in range(1, 5)}
    )
    project = m.Project(
        id="proj_retained", name="Synthetic retained project", created_at=old.clock()
    )
    old.insert(project)
    with old.connection() as conn:
        before = tuple(conn.iterdump())
    captures = {}
    original = core.Store._record_upgrade_backup

    def capture(self, result):
        original(self, result)
        captures[result.path] = file_hashes(result.path)

    monkeypatch.setattr(core.Store, "_record_upgrade_backup", capture)
    with writer_lock(old), old._busy_timeout(0.25):
        with pytest.raises(core._StorageBusy) as caught:
            core.Store(paths, schema_version=5, migrations=target_migrations)
        assert caught.value.sqlite_errorcode & 0xFF == sqlite3.SQLITE_BUSY
        with old.connection() as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
            assert tuple(conn.iterdump()) == before
    assert len(captures) == 1
    backup, hashes = next(iter(captures.items()))
    assert file_hashes(backup) == hashes
    with core._archive_connection(backup / "state.sqlite3") as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert tuple(conn.iterdump()) == before
        core._verify_schema(conn, 4, core.MIGRATIONS)
    assert old.pre_upgrade_backups()[0]["status"] == "complete"
    upgraded = core.Store(paths, schema_version=5, migrations=target_migrations)
    assert upgraded.get(m.Project, project.id) == project
    with upgraded.connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        core._verify_schema(conn, 5, core.MIGRATIONS)
        assert conn.execute("SELECT count(*) FROM muse_trust_observations").fetchone()[0] == 0
    assert file_hashes(backup) == hashes


@pytest.mark.parametrize("mode", ["standard", "hint", "rv"])
def test_claimed_muse_trust_never_inherits_unclaimed_one_second_scope(ctx, monkeypatch, mode):
    policy = SimpleNamespace(
        muse=SimpleNamespace(enabled=True), app=SimpleNamespace(network_mode="live")
    )
    monkeypatch.setattr(lifecycle, "load_config", lambda root: policy)
    monkeypatch.setattr(lifecycle, "BoundMuseCredentials", lambda *args: Credentials())
    monkeypatch.setattr(lifecycle, "MuseURLGuard", lambda *args: Guard())
    constructor = lifecycle.MuseTransport
    calls, unclaimed = [], []

    def factory(**kwargs):
        connector = Connecting(transform=lambda key, nonce: rv()) if mode == "rv" else Connecting()
        http = (
            FakeHttp({"/api/session": response(assignment(cvm_required=True))})
            if mode == "hint"
            else FakeHttp()
        )
        return constructor(**kwargs, connector=connector, http=http)

    monkeypatch.setattr(lifecycle, "MuseTransport", factory)
    for name in (
        "muse_freeze",
        "muse_assert_current",
        "muse_standard_handoff",
        "_muse_record_observation",
    ):
        original = getattr(core.Store, name)

        def observe(original, name):
            @wraps(original)
            def run(self, *args, **kwargs):
                assert core._busy_timeout_seconds() == 30
                calls.append((name, self))
                return original(self, *args, **kwargs)

            return run

        monkeypatch.setattr(core.Store, name, observe(original, name))

    def handler(step):
        discussion = SimpleNamespace(
            backend="live", provider="synthetic", account="synthetic", auth_ref="auth_" + "a" * 32
        )
        with lifecycle._gateway(step, discussion) as transport:
            assert transport._trust.store is step.store
            if mode == "standard":
                assert transport.sessions_list().status == 200
            else:
                with pytest.raises(m.CoreError) as caught:
                    transport.sessions_list()
                assert caught.value.detail.code == m.ErrorCode.PROTOCOL_MISMATCH
        return m.JobResult()

    operations = {}
    jobs.register_operation("fixture.muse_busy", handler, "none", registry=operations)
    jobs.submit_job(ctx.store, "fixture.muse_busy", "synthetic", {}, registry=operations)
    executor = jobs.Executor(ctx.store, registry=operations)
    retry = executor._retry_unclaimed

    def observed_retry(action):
        def observed_action():
            unclaimed.append(core._busy_timeout_seconds())
            return action()

        return retry(observed_action)

    monkeypatch.setattr(executor, "_retry_unclaimed", observed_retry)
    executor.store = executor._retry_unclaimed(ctx.store._sibling)
    assert not calls  # Sibling startup validates schema, not Muse expectation state.
    with executor:
        assert executor.run_once()
        assert calls and all(store is executor.store for _, store in calls)
    assert unclaimed and set(unclaimed) == {1}
    assert core._busy_timeout_seconds() == 30
    names = {name for name, _ in calls}
    assert ("_muse_record_observation" in names) == (mode != "standard")
    assert ("muse_standard_handoff" in names) == (mode == "standard")
