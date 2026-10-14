"""Unrelated trust components do not pay for a workspace-wide closure."""

import os
import statistics
import time

import pytest
from test_muse_round5_identity import attempt
from test_muse_standard import assignment, response
from metamuse._muse_http import SESSION_RESPONSE_MAX_BYTES, HttpResponse
from metamuse.models import CoreError, canonical_json


def cycle(store, label="unrelated"):
    start = time.process_time()
    snap = store.muse_freeze(
        provider="other",
        account=label,
        vm_id="ordinary",
        viewer_id="ordinary-owner",
        session_binding_id="ordinary-binding",
    )
    store.muse_assert_current(snap)
    handed = []
    store.muse_standard_handoff(snap, lambda: handed.append(True))
    assert handed and not snap.confidential_required
    return time.process_time() - start


def test_r5_4_unrelated_cycle_is_component_bounded(ctx):
    cycle(ctx.store)  # warm once-per-Store schema verification
    baseline = statistics.median(cycle(ctx.store) for _ in range(3))
    results = []
    for number in range(1, 5):
        prefix = chr(96 + number)
        payload = assignment(vm_id=prefix + "000000", cvm_required=True, vms=[])
        count = (SESSION_RESPONSE_MAX_BYTES - len(canonical_json(payload)) + 1) // 20
        ids = [f"{prefix}{i:06d}" for i in range(count)]
        payload["vms"] = [{"vm_id": identity} for identity in ids]
        body = canonical_json(payload)
        assert len(body) <= SESSION_RESPONSE_MAX_BYTES
        body += b" " * (SESSION_RESPONSE_MAX_BYTES - len(body))
        transport, http, connector = attempt(
            ctx.store, payload, owner="remote-" + prefix, label=prefix
        )
        http.responses["/api/session"] = HttpResponse(200, body, {}, response({}).peer)
        start = time.process_time()
        with pytest.raises(CoreError):
            transport.sessions_list()
        print(
            f"max-bind number={number} ids={count} cpu={time.process_time() - start:.6f} load={os.getloadavg()}"
        )
        assert not connector.urls
        if number in (1, 2, 4):
            spent = statistics.median(cycle(ctx.store) for _ in range(3))
            results.append(spent)
            print(
                f"unrelated binds={number} baseline={baseline:.6f} cpu={spent:.6f} ratio={spent / baseline:.3f}"
            )
        if number == 1:
            start = time.process_time()
            snap = ctx.store.muse_freeze(
                provider="same",
                account="same",
                vm_id=ids[0],
                viewer_id="remote-" + prefix,
                session_binding_id="same-session",
            )
            assert snap.confidential_required
            print(f"same-owner maximum cpu={time.process_time() - start:.6f}")
    assert max(results) / baseline <= 3
    # No increasing workspace-sized term; allow CPU measurement noise.
    assert results[-1] <= 1.5 * max(results[:2])
