"""Confidential propagation invalidates sockets across every local label."""

import pytest
from test_muse_transport import build, ok_frames

from metamuse._muse_trust import StoreMuseTrust
from metamuse.models import CoreError, ErrorCode


@pytest.mark.parametrize("path", ["hint", "rv"])
def test_r4_7_cross_label_confidential_write_closes_open_socket(ctx, monkeypatch, path):
    transport, http, _, connector = build(handler=ok_frames([]), store=ctx.store)
    transport._trust = StoreMuseTrust(ctx.store, "Synthetic", "A")
    with transport:
        transport.sessions_list()
        closed = []
        close = transport._loop.link.close

        def observed_close():
            closed.append(True)
            close()

        monkeypatch.setattr(transport._loop.link, "close", observed_close)
        snapshot = ctx.store.muse_freeze(
            provider="alias",
            account="B",
            vm_id="vm1",
            viewer_id="other-owner",
            session_binding_id="other-session",
        )
        if path == "rv":
            ctx.store.muse_record_confidential(snapshot)
        else:
            ctx.store.muse_freeze(
                provider="alias",
                account="B",
                vm_id="vm1",
                viewer_id="other-owner",
                session_binding_id="other-session",
                confidential=True,
            )
        assert transport._trust_invalidated
        assert closed
        dials = len(connector.urls)
        requests = len(http.calls)
        with pytest.raises(CoreError) as caught:
            transport.sessions_list()
        assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
        assert len(connector.urls) == dials
        assert len(http.calls) == requests
