"""A whole authenticated CVM-hint bind is set-wise, not one closure per id."""

import time

import pytest
from test_muse_standard import Connecting, assignment, response
from test_muse_transport import FakeHttp, build, ok_frames

from metamuse.models import CoreError, ErrorCode
from metamuse.paths import WorkspacePaths
from metamuse.store import Store


def hinted_transport(store, count):
    ids = [f"v{i:07d}" for i in range(count)]
    payload = assignment(vm_id=ids[0], cvm_required=True, vms=[{"vm_id": v} for v in ids])
    connector = Connecting()
    transport, *_ = build(
        handler=ok_frames([]),
        store=store,
        http=FakeHttp({"/api/session": response(payload)}),
        connector=connector,
    )
    return transport, connector, ids


def test_r4_1_hint_bind_cpu_growth_and_every_id(tmp_path):
    previous = None
    for count in (625, 1250, 2500, 5000):
        store = Store(WorkspacePaths.resolve(tmp_path / str(count)))
        transport, connector, ids = hinted_transport(store, count)
        with store._muse_boundary("synthetic", "synthetic"):
            pass  # Exclude the once-per-Store schema verification from the measurement.
        started = time.process_time()
        with pytest.raises(CoreError) as caught:
            transport.sessions_list()
        cpu = time.process_time() - started
        assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
        assert not connector.urls
        with store.connection() as conn:
            observations = dict(
                conn.execute("SELECT vm_id,confidential_required FROM muse_trust_observations")
            )
            vms = dict(conn.execute("SELECT vm_id,confidential_required FROM muse_trust_vms"))
            assert observations == dict.fromkeys(ids, 1)
            assert vms == dict.fromkeys(ids, 1)
            assert not conn.execute("SELECT * FROM muse_trust_propagating").fetchall()
        print(f"bind ids={count} cpu={cpu:.6f} ratio={cpu / previous if previous else 0:.3f}")
        if previous is not None:
            assert cpu / previous <= 2.5
        previous = cpu


def test_r4_1_one_closure_for_the_whole_hint_bind(ctx, monkeypatch):
    calls = []
    original = ctx.store._muse_close_bind

    def close(conn):
        calls.append(True)
        original(conn)

    monkeypatch.setattr(ctx.store, "_muse_close_bind", close)
    transport, connector, _ids = hinted_transport(ctx.store, 100)
    with pytest.raises(CoreError):
        transport.sessions_list()
    assert calls == [True]
    assert not connector.urls


def test_r4_1_maximum_session_body_records_all_ids(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from metamuse import store as store_module
    from metamuse._muse_http import HttpResponse, SESSION_RESPONSE_MAX_BYTES
    from metamuse.models import canonical_json

    store = Store(WorkspacePaths.resolve(tmp_path / "maximum"))
    payload = assignment(vm_id="v000000", cvm_required=True, vms=[])
    count = (SESSION_RESPONSE_MAX_BYTES - len(canonical_json(payload)) + 1) // 20
    ids = [f"v{i:06d}" for i in range(count)]
    payload["vms"] = [{"vm_id": v} for v in ids]
    body = canonical_json(payload)
    assert len(body) <= SESSION_RESPONSE_MAX_BYTES
    body += b" " * (SESSION_RESPONSE_MAX_BYTES - len(body))
    original = response(payload)
    http = FakeHttp({"/api/session": HttpResponse(200, body, {}, original.peer)})
    connector = Connecting()
    transport, *_ = build(handler=ok_frames([]), store=store, http=http, connector=connector)
    with store._muse_boundary("synthetic", "synthetic"):
        pass
    holds = []
    lock = store_module.file_lock

    @contextmanager
    def observed_lock(path, **kwargs):
        with lock(path, **kwargs):
            started = time.monotonic()
            yield
            if path.name == "muse-trust-domain.lock":
                holds.append(time.monotonic() - started)

    monkeypatch.setattr(store_module, "file_lock", observed_lock)
    started, wall = time.process_time(), time.monotonic()
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    cpu, elapsed = time.process_time() - started, time.monotonic() - wall
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert not connector.urls
    with store.connection() as conn:
        for table in ("muse_trust_observations", "muse_trust_vms"):
            assert tuple(
                conn.execute(f"SELECT count(*),sum(confidential_required) FROM {table}").fetchone()
            ) == (count, count)
    assert len(holds) == 2  # One bulk bind; one small post-commit label verification.
    print(
        f"maximum body={len(body)} ids={count} cpu={cpu:.6f} wall={elapsed:.6f} "
        f"domain_lock_holds={holds}"
    )
