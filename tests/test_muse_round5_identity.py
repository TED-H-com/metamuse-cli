"""All authenticated identity aliases agree before any account-side request."""

import pytest
from test_muse_standard import Connecting, assignment, response
from test_muse_transport import Credentials, FakeHttp, build, ok_frames

from metamuse.models import CoreError, ErrorCode

ALIASES = ["vmId", "VM_ID", "vm-id", "VmID"]


def attempt(store, payload, *, owner="viewer-fixture", label="synthetic"):
    http = FakeHttp(
        {
            "/api/session": response(payload),
            "/api/auth/check": response(
                {
                    "ok": True,
                    "access_token": "synthetic-access-42",
                    "viewer_id": owner,
                    "session_binding_id": "binding-" + owner,
                }
            ),
        }
    )
    creds = Credentials()
    creds.binding.account = label
    connector = Connecting()
    transport, *_ = build(
        handler=ok_frames([]), store=store, http=http, credentials=creds, connector=connector
    )
    return transport, http, connector


@pytest.mark.parametrize("alias", ALIASES)
@pytest.mark.parametrize("first", [True, False])
@pytest.mark.parametrize("where", ["session", "row"])
@pytest.mark.parametrize("value", ["cvmx", None, 42, "bad/id"])
def test_r5_1_identity_agreement(ctx, alias, first, where, value):
    # Leading aliases used to route vm1 while validating cvmx's name/address.
    row = assignment(vm_name="cvmx", endpoint_url="wss://cvmx.metaaivm.com/")
    row = dict([(alias, value), *row.items()] if first else [*row.items(), (alias, value)])
    payload = row if where == "session" else assignment(vms=[row])
    transport, http, connector = attempt(ctx.store, payload)
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert caught.value.detail.retry_class == "never"
    assert len(http.calls) == 2 and not connector.urls


@pytest.mark.parametrize("alias", ALIASES)
@pytest.mark.parametrize("first", [True, False])
@pytest.mark.parametrize("where", ["session", "row"])
def test_r5_1_hint_records_every_identity(ctx, alias, first, where):
    row = assignment(
        vm_id="vm9", vm_name="vm9", endpoint_url="wss://vm9.metaaivm.com/", vm_type="hatch_cvm"
    )
    row = dict([(alias, "cvmx"), *row.items()] if first else [*row.items(), (alias, "cvmx")])
    payload = row if where == "session" else assignment(vms=[row])
    transport, http, connector = attempt(ctx.store, payload)
    with pytest.raises(CoreError):
        transport.sessions_list()
    with ctx.store.connection() as conn:
        recorded = dict(
            conn.execute("SELECT vm_id,confidential_required FROM muse_trust_observations")
        )
    assert recorded.get("vm9") == recorded.get("cvmx") == 1
    later, _, later_connector = attempt(
        ctx.store,
        assignment(vm_id="vm9", vm_name="vm9", endpoint_url="wss://vm9.metaaivm.com/"),
        owner="another-owner",
        label="another-label",
    )
    with pytest.raises(CoreError) as caught:
        later.sessions_list()
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert not later_connector.urls and not connector.urls and len(http.calls) == 2


def test_r5_1_equivalent_aliases_keep_one_wire_identity(ctx):
    from urllib.parse import parse_qs, urlsplit
    import json

    row = assignment(vm_id=" VM1 ", vmId="vm1", VM_ID="Vm1")
    transport, http, connector = attempt(ctx.store, row)
    with transport:
        transport.sessions_list()
    assert parse_qs(urlsplit(connector.urls[0]).query)["vm_id"] == [" VM1 "]
    wake = next(c for c in http.calls if "/vm/wake" in c["url"])
    assert json.loads(wake["body"])["vm_id"] == " VM1 "
    with ctx.store.connection() as conn:
        assert conn.execute("SELECT active_vm_id FROM muse_trust_accounts").fetchone()[0] == "vm1"
