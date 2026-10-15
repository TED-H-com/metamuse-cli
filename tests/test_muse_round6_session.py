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

@pytest.mark.parametrize(
    "pair",
    [
        '"vm_type":"hatch_cvm","vm_type":"hatch_vm"',
        '"cvm_required":true,"cvm_required":false',
        '"vm_id":"vm9","vm_id":"vm1","kind":"hatch_cvm"',
    ],
)
def test_r6_6_duplicate_evidence_is_not_lost(ctx, pair):
    body = json.dumps(assignment())[:-1] + "," + pair + "}"
    refuse(ctx, {}, raw=body.encode(), ids={"vm1", "vm9"} if "vm9" in pair else {"vm1"})

@pytest.mark.parametrize("where", ["session", "child"])
def test_r6_6_duplicate_without_marker_refused(ctx, where):
    pair = '"vm_id":"vm9","vm_id":"vm1"'
    body = (
        json.dumps(assignment())[:-1]
        + ","
        + (pair if where == "session" else '"meta":{' + pair + "}")
        + "}"
    )
    refuse(ctx, {}, raw=body.encode(), ids=set())

@pytest.mark.parametrize(
    "encoding",
    ["utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le", "utf-32-be", "utf-8-sig"],
)
@pytest.mark.parametrize("deep", [False, True])
def test_r6_7_only_strict_utf8(ctx, encoding, deep):
    doc = assignment(vm_type="hatch_cvm", a="丢")
    if deep:
        node = []
        for _ in range(40):
            node = [node]
        doc["deep"] = node
    refuse(ctx, {}, raw=json.dumps(doc, ensure_ascii=False).encode(encoding), ids=set())

def test_r6_7_invalid_utf8(ctx):
    refuse(ctx, {}, raw=b'{"note":"\xff","vm_type":"hatch_cvm"}', ids=set())

@pytest.mark.parametrize("shape", range(4))
def test_r6_8_union_of_enclosing_identity_scopes(ctx, shape):
    meta = {"vm_id": "vm2", "kind": "HATCH-CVM"}
    if shape == 0:
        payload = assignment(vms=[{**assignment(), "meta": meta}])
    elif shape == 1:
        payload = assignment(meta=meta)
    elif shape == 2:
        payload = assignment(vms=[assignment(), {"vm_id": "vm7"}], pending={"vm_type": "hatch_cvm"})
    else:
        payload = assignment(vms=[assignment(), {"vm_id": "vm7"}], vm_type="hatch_cvm")
    refuse(ctx, payload, ids={"vm1", "vm2"} if shape < 2 else {"vm1", "vm7"})

@pytest.mark.parametrize("path", ["auth", "hatch"])
def test_r6_6_other_https_duplicate_keys(ctx, path):
    transport, http, connector = attempt(ctx.store, assignment())
    route = "/api/auth/check" if path == "auth" else "/api/hatch/token"
    raw = b'{"ok":true,"ok":true,"token":"synthetic"}'
    http.responses[route] = HttpResponse(200, raw, {}, response({}).peer)
    with pytest.raises(CoreError) as error:
        transport.sessions_list()
    assert error.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert not connector.urls

def test_r6_8_outermost_scope_cannot_claim_unmarked_decoy(ctx):
    payload = assignment(
        vms=[{**assignment(), "meta": {"vm_id": "vm2", "kind": "hatch_cvm"}}, {"vm_id": "vm7"}]
    )
    refuse(ctx, payload, ids={"vm1", "vm2"})
    later, _, dial = attempt(
        ctx.store,
        assignment(vm_id="vm7", vm_name="vm7", endpoint_url="wss://vm7.metaaivm.com/"),
        owner="unmarked-owner",
        label="unmarked",
    )
    with later:
        later.sessions_list()
    assert dial.urls

@pytest.mark.parametrize("encoding", ["utf-8", "utf-16-le", "utf-32-le"])
@pytest.mark.parametrize("depth", [0, 1, 30, 31, 32, 33, 40])
def test_r6_7_public_parser_makes_walk_guard_unreachable(ctx, encoding, depth):
    node = {"kind": "hatch_cvm"}
    for _ in range(depth):
        node = [node]
    payload = assignment(meta=node, a="丢")
    body = json.dumps(payload, ensure_ascii=False).encode(encoding)
    transport, http, connector = attempt(ctx.store, {})
    http.responses["/api/session"] = HttpResponse(200, body, {}, response({}).peer)
    with pytest.raises(CoreError):
        transport.sessions_list()
    # JSON nesting includes root+meta object; UTF8 must be <=32 before the walk.
    assert bool(observed(ctx.store)) == (encoding == "utf-8" and depth + 2 <= 32)
    assert not connector.urls
