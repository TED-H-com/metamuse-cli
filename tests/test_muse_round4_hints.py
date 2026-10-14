"""Nested, compact hint spellings persist before any selection refusal."""

import pytest
from test_muse_standard import Connecting, assignment, response
from test_muse_transport import FakeHttp, build, ok_frames

from metamuse.models import CoreError, ErrorCode


def selected_row(**changes):
    row = {
        "vm_id": "vm1",
        "vm_name": "vm1",
        "endpoint_url": "wss://vm1.metaaivm.com/",
        "vm_type": "hatch_vm",
        "vm_state": "RUNNING",
    }
    row.update(changes)
    return row


VARIANTS = {
    # round-2 HS1 set
    "exact_hatch_cvm": lambda: assignment(vm_type="hatch_cvm"),
    "upper_HATCH_CVM": lambda: assignment(vm_type="HATCH_CVM"),
    "spaced_hatch_cvm": lambda: assignment(vm_type=" hatch_cvm"),
    "lower_cvm_state": lambda: assignment(vm_state="cvm_running"),
    "camel_cvmRequired": lambda: assignment(cvmRequired=True),
    "snake_cvm_replacement_enabled": lambda: assignment(cvm_replacement_enabled=True),
    # new value spellings
    "hyphen_hatch-cvm": lambda: assignment(vm_type="hatch-cvm"),
    "space_HATCH_CVM_words": lambda: assignment(vm_type="HATCH CVM"),
    "joined_hatchcvm": lambda: assignment(vm_type="hatchcvm"),
    "type_cvm_only": lambda: assignment(vm_type="cvm"),
    "type_confidential": lambda: assignment(vm_type="hatch_confidential_vm"),
    "state_CVM-RUNNING": lambda: assignment(vm_state="CVM-RUNNING"),
    "state_cvm_space": lambda: assignment(vm_state="cvm running"),
    "required_string_true": lambda: assignment(cvm_required="true"),
    "required_int_1": lambda: assignment(cvm_required=1),
    # new key spellings / locations
    "key_vmType": lambda: {**assignment(), "vmType": "hatch_cvm"},
    "key_type": lambda: {**assignment(), "type": "hatch_cvm"},
    "key_vm-type_hyphen": lambda: {**assignment(), "vm-type": "hatch_cvm"},
    "nested_vm_object": lambda: {**assignment(), "vm": {"vm_type": "hatch_cvm"}},
    "nested_in_row": lambda: assignment(vms=[{**selected_row(), "meta": {"vm_type": "hatch_cvm"}}]),
    "other_row_drift_type": lambda: assignment(
        vms=[selected_row(), {"vm_id": "vm2", "vm_type": "HATCH-CVM"}]
    ),
    "other_row_vmId_key": lambda: assignment(
        vms=[selected_row(), {"vmId": "vm2", "vm_type": "hatch_cvm"}]
    ),
    "row_list_nested": lambda: assignment(
        vms=[[{"vm_id": "vm2", "vm_type": "hatch_cvm"}], selected_row()]
    ),
    "vms_as_object": lambda: assignment(vms={"vm2": {"vm_id": "vm2", "vm_type": "hatch_cvm"}}),
    "vm_resolution_issue_cvm": lambda: {
        **assignment(),
        "vm_resolution_issue": {"kind": "cvm_required"},
    },
}


@pytest.mark.parametrize("name", list(VARIANTS))
def test_r4_8_every_review_hint_is_sticky(ctx, name):
    http = FakeHttp({"/api/session": response(VARIANTS[name]())})
    connector = Connecting()
    transport, *_ = build(handler=ok_frames([]), http=http, connector=connector, store=ctx.store)
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert not connector.urls
    with ctx.store.connection() as conn:
        assert conn.execute(
            "SELECT 1 FROM muse_trust_observations WHERE confidential_required=1"
        ).fetchone()
    http.responses["/api/session"] = response(assignment())
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert not connector.urls


@pytest.mark.parametrize(
    "key",
    [
        "vm_type",
        "type",
        "vm_state",
        "state",
        "kind",
        "vm_kind",
        "vm_resolution_issue",
        "cvm_required",
        "cvm_replacement_enabled",
    ],
)
def test_r4_8_fixed_hint_key_family_at_any_depth(ctx, key):
    payload = assignment(meta={"deeper": {key.upper().replace("_", "- "): "HaTcH Confidential-VM"}})
    connector = Connecting()
    transport, *_ = build(
        handler=ok_frames([]),
        http=FakeHttp({"/api/session": response(payload)}),
        connector=connector,
        store=ctx.store,
    )
    with pytest.raises(CoreError):
        transport.sessions_list()
    with ctx.store.connection() as conn:
        assert dict(
            conn.execute("SELECT vm_id,confidential_required FROM muse_trust_observations")
        ) == {"vm1": 1}
    assert not connector.urls


def test_r4_8_marker_inside_row_records_only_that_row(ctx):
    payload = assignment(vms=[selected_row(), {"vmId": " VM2 ", "meta": {"kind": "HATCH-CVM"}}])
    connector = Connecting()
    transport, *_ = build(
        handler=ok_frames([]),
        http=FakeHttp({"/api/session": response(payload)}),
        connector=connector,
        store=ctx.store,
    )
    with pytest.raises(CoreError):
        transport.sessions_list()
    with ctx.store.connection() as conn:
        assert dict(
            conn.execute("SELECT vm_id,confidential_required FROM muse_trust_observations")
        ) == {"vm2": 1}
    assert not connector.urls


def test_r4_8_row_identity_follows_innermost_identity(ctx):
    payload = assignment(vms=[{**selected_row(), "meta": {"vm_id": "vm2", "kind": "HATCH-CVM"}}])
    connector = Connecting()
    transport, *_ = build(
        handler=ok_frames([]),
        http=FakeHttp({"/api/session": response(payload)}),
        connector=connector,
        store=ctx.store,
    )
    with pytest.raises(CoreError):
        transport.sessions_list()
    with ctx.store.connection() as conn:
        assert dict(
            conn.execute("SELECT vm_id,confidential_required FROM muse_trust_observations")
        ) == {"vm2": 1}
    # A fresh label/owner must inherit the observed row's confidential expectation.
    snapshot = ctx.store.muse_freeze(
        provider="alias",
        account="later",
        viewer_id="other-owner",
        session_binding_id="other-session",
        vm_id="vm2",
    )
    assert snapshot.confidential_required
    sent = []
    with pytest.raises(CoreError) as caught:
        ctx.store.muse_standard_handoff(snapshot, lambda: sent.append(True))
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert not sent and not connector.urls
