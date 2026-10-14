"""Every HS1 hint spelling is sticky across a later standard assignment."""

import pytest
from test_muse_standard import Connecting, assignment, response
from test_muse_transport import FakeHttp, build, ok_frames

from metamuse.models import CoreError, ErrorCode


@pytest.mark.parametrize(
    "change",
    [
        {"vm_type": "hatch_cvm"},
        {"vm_type": "HATCH_CVM"},
        {"vm_type": " hatch_cvm "},
        {"vm_state": "cvm_running"},
        {"vm_state": " CVM_PROVISIONING "},
        {"cvmRequired": True},
        {"cvm_replacement_enabled": True},
        {"CVM-REQUIRED": 1},
        {"Cvm-Replacement-Enabled": True},
        {"VM-TYPE": " HATCH_CVM "},
    ],
)
def test_r3_j_hint_spellings_are_sticky(ctx, change):
    connector = Connecting()
    http = FakeHttp({"/api/session": response(assignment(**change))})
    transport, _, _, _ = build(
        handler=ok_frames([]), connector=connector, store=ctx.store, http=http
    )
    for _ in range(2):
        with pytest.raises(CoreError) as caught:
            transport.sessions_list()
        assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
        assert not connector.urls
        http.responses["/api/session"] = response(assignment())
    with ctx.store.connection() as conn:
        assert (
            conn.execute(
                "SELECT confidential_required FROM muse_trust_accounts WHERE account='synthetic'"
            ).fetchone()[0]
            == 1
        )
