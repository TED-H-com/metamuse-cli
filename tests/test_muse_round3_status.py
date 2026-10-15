"""HTTP success shape drift never masquerades as credential expiry."""

import pytest
from test_muse_standard import Connecting, response
from test_muse_transport import FakeHttp, build, ok_frames

from metamuse.models import CoreError, ErrorCode


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/api/auth/check", {"ok": True}),
        ("/api/auth/check", {"ok": True, "access_token": 4}),
        (
            "/api/auth/check",
            {
                "ok": True,
                "access_token": "synthetic-token",
                "viewer_id": 4,
                "session_binding_id": "s",
            },
        ),
        ("/api/auth/check", {"ok": True, "access_token": "synthetic-token", "viewer_id": "v"}),
        ("/api/hatch/token", {}),
        ("/api/hatch/token", {"token": 4}),
        ("/api/hatch/token", {"token": "legacy:drift"}),
    ],
)
def test_r3_e_success_shape_drift_is_protocol_never(ctx, path, payload):
    connector = Connecting()
    transport, _, _, _ = build(
        handler=ok_frames([]),
        store=ctx.store,
        http=FakeHttp({path: response(payload)}),
        connector=connector,
    )
    with pytest.raises(CoreError) as caught:
        with transport:
            transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert caught.value.detail.retry_class == "never"
    assert not connector.urls


def test_r3_e_explicit_auth_refusal_remains_auth(ctx):
    transport, _, _, connector = build(
        handler=ok_frames([]),
        store=ctx.store,
        http=FakeHttp({"/api/auth/check": response({"ok": False})}),
        connector=Connecting(),
    )
    with pytest.raises(CoreError) as caught:
        with transport:
            transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.AUTH_REQUIRED
    assert not connector.urls
