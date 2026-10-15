"""Confidential writes and handoffs pay only for their connected component."""

import os
import statistics
import time

import pytest
from test_muse_round5_identity import attempt
from test_muse_standard import assignment, response
from test_muse_trust import freeze

from metamuse._muse_http import SESSION_RESPONSE_MAX_BYTES, HttpResponse
from metamuse.models import CoreError, canonical_json


def test_r6_5_confidential_operations_component_bounded(ctx):
    serial = iter(range(1000))

    def operations():
        result = []
        for kind in ("hint", "rv", "inherit", "standard"):
            n = next(serial)
            label = f"{kind}{n}"
            owner = f"new-owner{n}"
            vm = f"new-vm{n}"
            if kind == "rv":
                snap = freeze(ctx.store, account=label, viewer_id=owner, vm_id=vm)
            start = time.process_time()
            if kind == "hint":
                freeze(ctx.store, account=label, viewer_id=owner, vm_id=vm, confidential=True)
            elif kind == "rv":
                ctx.store.muse_record_confidential(snap)
            else:
                snap = freeze(
                    ctx.store,
                    account=label,
                    viewer_id=owner,
                    vm_id="seed" if kind == "inherit" else vm,
                )
                if kind == "inherit":
                    assert snap.confidential_required
                else:
                    ctx.store.muse_standard_handoff(snap, lambda: None)
            result.append(time.process_time() - start)
        return result

    freeze(ctx.store, account="seed", viewer_id="seed-owner", vm_id="seed", confidential=True)
    operations()

    def sample():
        samples = [operations() for _ in range(3)]
        return [statistics.median(row[i] for row in samples) for i in range(4)]

    baseline = sample()
    rows = []
    for number in range(1, 5):
        prefix = chr(96 + number)
        payload = assignment(vm_id=prefix + "000000", cvm_required=True, vms=[])
        count = (SESSION_RESPONSE_MAX_BYTES - len(canonical_json(payload)) + 1) // 20
        ids = [f"{prefix}{i:06d}" for i in range(count)]
        payload["vms"] = [{"vm_id": identity} for identity in ids]
        body = canonical_json(payload).ljust(SESSION_RESPONSE_MAX_BYTES, b" ")
        transport, http, connector = attempt(
            ctx.store, payload, owner="remote-" + prefix, label=prefix
        )
        http.responses["/api/session"] = HttpResponse(200, body, {}, response({}).peer)
        start = time.process_time()
        with pytest.raises(CoreError):
            transport.sessions_list()
        assert not connector.urls
        print(
            f"r6-max-bind k={number} ids={count} cpu={time.process_time() - start:.6f} "
            f"load={os.getloadavg()}",
            flush=True,
        )
        if number in (1, 2, 4):
            spent = sample()
            rows.append(spent)
            ratios = [b / a for a, b in zip(baseline, spent, strict=True)]
            print(
                f"r6-cost k={number} baseline={baseline} cpu={spent} ratios={ratios}",
                flush=True,
            )
        if number == 1:
            start = time.process_time()
            snap = freeze(
                ctx.store, account="same-new-label", viewer_id="remote-" + prefix, vm_id=ids[0]
            )
            assert snap.confidential_required
            print(f"r6-same-owner ids={count} cpu={time.process_time() - start:.6f}", flush=True)
    assert all(max(row[i] for row in rows) / baseline[i] <= 3 for i in range(4)), (baseline, rows)
    assert all(rows[-1][i] <= 1.5 * max(row[i] for row in rows[:2]) for i in range(4)), rows
