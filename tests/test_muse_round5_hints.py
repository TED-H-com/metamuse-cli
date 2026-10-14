"""Every residual marker persists at its innermost authenticated identity scope."""

import pytest
from test_muse_round5_identity import attempt
from test_muse_standard import assignment

from metamuse.models import CoreError, ErrorCode

SHAPES = [
    {"vm_type": ["hatch_cvm"]},
    {"vm_state": ["CVM_RUNNING"]},
    {"vm_state": {"name": "CVM_RUNNING"}},
    {"cvm": True},
    {"is_cvm": True},
    {"cvm_enabled": True},
    {"confidential": True},
    {"vm_flavor": "cvm"},
    {"vm_type": "confidential"},
    {"vm_type": "hatch_ｃｖｍ"},
    {"vm_type": "hatch_c\u200bvm"},
    {"meta": ["ordinary", ["hatch_cvm"]]},
    {"ｍｙ＿ｃｖｍ": "yes"},
    {"nested": {"deep": {"confidential_flag": {"enabled": 1}}}},
]


@pytest.mark.parametrize("extra", SHAPES)
def test_r5_8_residual_markers_are_sticky(ctx, extra):
    transport, _, connector = attempt(ctx.store, assignment(**extra))
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    with ctx.store.connection() as conn:
        assert conn.execute(
            "SELECT 1 FROM muse_trust_observations WHERE vm_id='vm1' AND confidential_required=1"
        ).fetchone()
    later, _, later_connector = attempt(ctx.store, assignment(), owner="other-owner", label="other")
    with pytest.raises(CoreError):
        later.sessions_list()
    assert not connector.urls and not later_connector.urls


@pytest.mark.parametrize("value", [False, 0, None, "", [], {}, "false", "NO", " 0 ", "none"])
@pytest.mark.parametrize("key", ["cvm_required", "cvm_replacement_enabled", "is_cvm"])
def test_r5_8_false_marker_keys_are_not_evidence(ctx, key, value):
    transport, _, _ = attempt(ctx.store, assignment(**{key: value}))
    # Selection may independently refuse an invalid replacement shape; no sticky evidence.
    try:
        with transport:
            transport.sessions_list()
    except CoreError:
        pass
    with ctx.store.connection() as conn:
        assert not conn.execute(
            "SELECT 1 FROM muse_trust_observations WHERE confidential_required=1"
        ).fetchone()


@pytest.mark.parametrize(
    "rows,expected",
    [
        ({"vm_id": "decoy", "rows": [{"vm_id": "vm9", "vm_type": "hatch_cvm"}]}, {"vm9"}),
        ([{"vm_id": "host", "children": [{"vm_id": "vm9", "vm_type": "hatch_cvm"}]}], {"vm9"}),
        ([{"vm_type": "hatch_cvm", "replaces": {"vm_id": "vm9"}}], {"vm1", "vm9"}),
        ([{"vmId": "decoy", "vm_id": "vm9", "vm_type": "hatch_cvm"}], {"decoy", "vm9"}),
        ([{"vm_id": "host", "meta": {"vm_id": "vm9", "kind": "hatch_cvm"}}], {"vm9"}),
    ],
)
def test_r5_8_innermost_row_attribution(ctx, rows, expected):
    transport, _, connector = attempt(ctx.store, assignment(vms=rows))
    with pytest.raises(CoreError):
        transport.sessions_list()
    with ctx.store.connection() as conn:
        rows = conn.execute(
            "SELECT vm_id,confidential_required FROM muse_trust_observations"
        ).fetchall()
        assert {r[0] for r in rows} == expected
        assert all(r[1] == 1 for r in rows)
    assert not connector.urls


def test_r5_8_owner_only_mismatch_records_before_refusal(ctx):
    from test_muse_trust import freeze

    freeze(ctx.store)
    transport, _, connector = attempt(
        ctx.store,
        {"status": "unavailable", "meta": ["hatch_cvm"]},
        owner="other-owner",
        label="account",
    )
    with pytest.raises(CoreError):
        transport.sessions_list()
    # Mismatch label must not hide owner-only observation from a later label.
    later, _, later_connector = attempt(ctx.store, assignment(), owner="other-owner", label="new")
    with pytest.raises(CoreError) as caught:
        later.sessions_list()
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert not connector.urls and not later_connector.urls
