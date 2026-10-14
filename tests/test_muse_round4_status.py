"""Successful HTTP with drifted session shape is a terminal interface failure."""

import pytest
from test_muse_standard import Connecting, assignment, response
from test_muse_transport import FakeHttp, build, ok_frames

from metamuse.models import CoreError, ErrorCode


@pytest.mark.parametrize("status", [None, "renamed", 1, "ASSIGNED", [], "pending"])
def test_r4_4_session_status_drift_never_retries(ctx, status):
    payload = assignment()
    if status is None:
        payload.pop("status")
    elif status == "renamed":
        payload["session_status"] = payload.pop("status")
    else:
        payload["status"] = status
    connector = Connecting()
    transport, *_ = build(
        handler=ok_frames([]),
        store=ctx.store,
        connector=connector,
        http=FakeHttp({"/api/session": response(payload)}),
    )
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert caught.value.detail.retry_class == "never"
    assert not connector.urls


@pytest.mark.parametrize(
    "kind,path",
    [("session", "/api/session"), ("hatch", "/api/hatch/token"), ("auth", "/api/auth/check")],
)
def test_r4_4_non_object_names_its_endpoint(ctx, kind, path):
    connector = Connecting()
    transport, *_ = build(
        handler=ok_frames([]),
        store=ctx.store,
        connector=connector,
        http=FakeHttp({path: response([])}),
    )
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert f"damaged {kind} response" in caught.value.detail.next_action
    assert not connector.urls


@pytest.mark.parametrize("missing", [False, True])
def test_r4_4_unavailable_session_keeps_resource_error(ctx, missing):
    payload = assignment(status="unavailable")
    if missing:
        payload = assignment()
        payload.pop("vm_id")
    connector = Connecting()
    transport, *_ = build(
        handler=ok_frames([]),
        store=ctx.store,
        connector=connector,
        http=FakeHttp({"/api/session": response(payload)}),
    )
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.RESOURCE_UNAVAILABLE
    assert not connector.urls
