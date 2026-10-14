"""Remote trust invariants, independently reviewed synthetic regressions."""

import pytest
from test_muse_standard import Connecting
from test_muse_transport import build, ok_frames
from test_muse_trust import freeze

from metamuse._muse_trust import StoreMuseTrust
from metamuse.models import CoreError, ErrorCode


def refused(store, snapshot):
    sent = []
    with pytest.raises(CoreError) as caught:
        store.muse_standard_handoff(snapshot, lambda: sent.append("msg3"))
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert caught.value.detail.retry_class == "never"
    assert not sent


@pytest.mark.parametrize("provider", ["Synthetic", "synthetic ", "synthetic.alias"])
@pytest.mark.parametrize("identity", ["owner", "vm"])
@pytest.mark.parametrize("record", ["freeze", "rv"])
def test_r3_a_provider_labels_never_partition_remote_trust(ctx, provider, identity, record):
    first = freeze(ctx.store, confidential=record == "freeze")
    if record == "rv":
        ctx.store.muse_record_confidential(first)
    other = freeze(
        ctx.store,
        provider=provider,
        account="other-label",
        viewer_id="viewer" if identity == "owner" else "other-owner",
        vm_id="other-vm" if identity == "owner" else " VM1 ",
    )
    assert other.confidential_required
    refused(ctx.store, other)


@pytest.mark.parametrize("provider", ["Synthetic", "synthetic ", "synthetic.alias"])
@pytest.mark.parametrize("identity", ["owner", "vm"])
@pytest.mark.parametrize("record", ["freeze", "rv"])
def test_r3_a_transport_refuses_provider_alias_before_msg3(ctx, provider, identity, record):
    first = freeze(
        ctx.store,
        account="original",
        viewer_id="viewer-fixture",
        vm_id="other-vm" if identity == "owner" else "vm1",
        confidential=record == "freeze",
    )
    if record == "rv":
        ctx.store.muse_record_confidential(first)
    connector = Connecting()
    transport, _, _, _ = build(handler=ok_frames([]), connector=connector, store=ctx.store)
    transport._trust = StoreMuseTrust(ctx.store, provider, "other-label")
    if identity == "vm":
        from test_muse_standard import response

        transport._http.responses["/api/auth/check"] = response(
            {
                "ok": True,
                "access_token": "synthetic-token",
                "viewer_id": "other-owner",
                "session_binding_id": "binding-fixture",
            }
        )
    with pytest.raises(CoreError) as caught:
        with transport:
            transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert not connector.urls


@pytest.mark.parametrize("identity", ["owner", "vm"])
def test_r3_b_stale_rv_snapshot_records_before_refusal(ctx, identity):
    from metamuse.store import Store

    stale = freeze(ctx.store)
    freeze(Store(ctx.paths), session_binding_id="changed")
    ctx.store.muse_record_confidential(stale)
    later = freeze(
        ctx.store,
        account="later",
        viewer_id="viewer" if identity == "owner" else "other",
        vm_id="other" if identity == "owner" else "vm1",
    )
    assert later.confidential_required
    refused(ctx.store, later)


def test_r3_b_transport_records_rv_after_cross_process_staleness(ctx):
    from test_muse_standard import rv

    from metamuse.store import Store

    connector = Connecting(transform=lambda key, nonce: rv())
    transport, _, _, _ = build(handler=ok_frames([]), connector=connector, store=ctx.store)
    connector.kwargs["before_reply"] = lambda: freeze(
        Store(ctx.paths),
        account="synthetic",
        viewer_id="viewer-fixture",
        session_binding_id="concurrent",
    )
    with pytest.raises(CoreError) as caught:
        with transport:
            transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert connector.peers[0].msg3_payload is None
    later = freeze(
        ctx.store, provider="alias", account="later", viewer_id="viewer-fixture", vm_id="other"
    )
    assert later.confidential_required
    refused(ctx.store, later)


def test_r3_b_record_failure_stops_with_honest_error(ctx, monkeypatch):
    connector = Connecting()
    from test_muse_standard import rv

    connector.kwargs["transform"] = lambda key, nonce: rv()
    transport, _, _, _ = build(handler=ok_frames([]), connector=connector, store=ctx.store)

    def cannot_record(*args, **kwargs):
        raise CoreError(ErrorCode.STORAGE, "synthetic busy")

    monkeypatch.setattr(ctx.store, "_muse_observe", cannot_record)
    with pytest.raises(CoreError) as caught:
        with transport:
            transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.STORAGE
    assert "confidentiality could not be recorded" in caught.value.detail.message
    assert connector.peers[0].msg3_payload is None


@pytest.mark.parametrize("identity", ["owner", "vm"])
def test_r3_c_hint_owner_mismatch_is_recorded_before_binding_refusal(ctx, identity):
    freeze(ctx.store, viewer_id="old-owner")
    with pytest.raises(CoreError) as caught:
        freeze(
            ctx.store,
            viewer_id="observed-owner",
            vm_id="cvm1",
            confidential=True,
            confidential_vm_ids=("cvm1", "cvm2", "cvm3"),
        )
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    later = freeze(
        ctx.store,
        provider="another",
        account="new-label",
        viewer_id="observed-owner" if identity == "owner" else "third-owner",
        vm_id="new-vm" if identity == "owner" else "cvm2",
    )
    assert later.confidential_required
    refused(ctx.store, later)


@pytest.mark.parametrize("identity", ["owner", "vm"])
def test_r3_c_transport_keeps_hint_under_mismatched_owner(ctx, identity):
    from test_muse_standard import assignment, response
    from test_muse_transport import FakeHttp

    freeze(ctx.store, account="synthetic", viewer_id="old-owner")
    http = FakeHttp(
        {
            "/api/session": response(
                assignment(
                    status="unassigned",
                    vms=[{"vm_id": " CVM2 ", "vm_type": "hatch_cvm"}, {"vm_id": "cvm3"}],
                )
            )
        }
    )
    connector = Connecting()
    transport, _, _, _ = build(
        handler=ok_frames([]), store=ctx.store, connector=connector, http=http
    )
    with pytest.raises(CoreError) as caught:
        with transport:
            transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert not connector.urls
    later = freeze(
        ctx.store,
        provider="another",
        account="later",
        viewer_id="viewer-fixture" if identity == "owner" else "third-owner",
        vm_id="vm-new" if identity == "owner" else "cvm2",
    )
    assert later.confidential_required
    refused(ctx.store, later)
