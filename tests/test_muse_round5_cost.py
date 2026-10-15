"""Unrelated trust components do not pay for a workspace-wide closure."""

import os
import time

import pytest
from cpu_growth import sample_cpu
from test_muse_round5_identity import attempt
from test_muse_standard import assignment, response

from metamuse._muse_http import SESSION_RESPONSE_MAX_BYTES, HttpResponse
from metamuse.models import CoreError, canonical_json


def cycle(store, label="unrelated"):
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


def test_r5_4_unrelated_cycle_is_component_bounded(ctx):
    cycle(ctx.store)  # warm once-per-Store schema verification

    def sample(stage):
        # Fixed batches lift the baseline above 20 ms; minima reject transient
        # CPU noise without allowing unrelated components to increase the cost.
        samples = [sample_cpu(lambda: cycle(ctx.store), repeats=4) for _ in range(5)]
        print(f"r5-cost-samples stage={stage} batch=4 samples={samples}", flush=True)
        return min(samples)

    baseline = sample("baseline")
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
            f"max-bind number={number} ids={count} cpu={time.process_time() - start:.6f} "
            f"load={os.getloadavg()}"
        )
        assert not connector.urls
        if number in (1, 2, 4):
            spent = sample(number)
            results.append(spent)
            print(
                f"unrelated binds={number} baseline={baseline:.6f} cpu={spent:.6f} "
                f"ratio={spent / baseline:.3f}"
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
    print(f"r5-cost-trend ratio={results[-1] / max(results[:2]):.6f}", flush=True)
    assert results[-1] <= 1.5 * max(results[:2])
