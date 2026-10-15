"""UTF-8 evidence-first parsing and union of enclosing remote identity scopes."""

import json

import pytest

from test_muse_round5_identity import attempt

from test_muse_standard import assignment, response

from metamuse._muse_http import HttpResponse

from metamuse.models import CoreError, ErrorCode

def observed(store):
    with store.connection() as conn:
        return {
            row[0]
            for row in conn.execute(
                "SELECT vm_id FROM muse_trust_observations WHERE confidential_required=1"
            )
        }

def refuse(ctx, payload, *, ids=None, raw=None):
    transport, http, connector = attempt(ctx.store, payload)
    if raw is not None:
        http.responses["/api/session"] = HttpResponse(200, raw, {}, response({}).peer)
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert caught.value.detail.retry_class == "never"
    assert len(http.calls) == 2 and not connector.urls
    if ids is not None:
        assert observed(ctx.store) == ids
        for identity in ids:
            if not identity:
                continue
            later, _, dial = attempt(
                ctx.store,
                assignment(
                    vm_id=identity, vm_name=identity, endpoint_url=f"wss://{identity}.metaaivm.com/"
                ),
                owner="later-" + identity,
                label="later-" + identity,
            )
            with pytest.raises(CoreError) as error:
                later.sessions_list()
            assert error.value.detail.code == ErrorCode.PROTOCOL_MISMATCH and not dial.urls

MARKERS = [
    "hatch_cvm",
    "HATCH-CVM",
    "hatchCvm",
    "cvm1",
    "CVM_RUNNING",
    "CVM_UNKNOWN",
    "confidential_vm",
    "non-confidential",
    "nonconfidential",
    "not cvm",
    "no-cvm",
    "ｃｖｍ",
    "c\u200bvm",
    "cⅴm",
    "ᶜvm",
    "𝐜𝐯𝐦",
]

NOT_MARKERS = [
    "Public VM",
    "basic vm",
    "/etc/vm/config",
    "abc-vm1",
    "spec VM 2",
    "us-c-vm-2",
    "xQ7cVm2R",
    "live",
    "cache",
    "C V M",
    "c-vm",
    "hatchcvm",
    "HATCHCVM",
]

@pytest.mark.parametrize("where", ["session", "row", "child"])
@pytest.mark.parametrize("value", MARKERS)
def test_r6_4_component_markers_sticky(ctx, where, value):
    row = assignment()
    if where == "session":
        payload = assignment(note=value)
    elif where == "row":
        payload = assignment(vms=[{**row, "note": value}])
    else:
        payload = assignment(meta={"child": {"note": value}})
    refuse(ctx, payload, ids={"vm1"})

@pytest.mark.parametrize("where", ["session", "row", "child"])
@pytest.mark.parametrize("value", NOT_MARKERS)
def test_r6_4_no_cross_word_marker(ctx, where, value):
    row = assignment()
    payload = assignment(
        **(
            {"note": value}
            if where == "session"
            else {"vms": [{**row, "note": value}]}
            if where == "row"
            else {"meta": {"note": value}}
        )
    )
    transport, _, connector = attempt(ctx.store, payload)
    with transport:
        transport.sessions_list()
    assert connector.urls and not observed(ctx.store)

@pytest.mark.parametrize(
    "extra",
    [
        {"public_vm_count": 3},
        {"dynamic_vm": True},
        {"c_v_m": True},
        {"vm_state_source": "live"},
        {"vm_state_source": "cache"},
        {"token": "cvm1"},
        {"token": ["cvm1", "hatch_cvm"]},
        {"cvm_required": False},
        {"cvm_replacement_enabled": False},
    ],
)
@pytest.mark.parametrize("where", ["session", "row", "child"])
def test_r6_4_opaque_values_and_unmarked_keys(ctx, extra, where):
    payload = (
        assignment(**extra)
        if where == "session"
        else assignment(vms=[{**assignment(), **extra}])
        if where == "row"
        else assignment(meta=extra)
    )
    transport, _, connector = attempt(ctx.store, payload)
    with transport:
        transport.sessions_list()
    assert connector.urls and not observed(ctx.store)
