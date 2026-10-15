"""Applicable privacy refusals survive the inapplicable-preview fallback."""

import pytest

from metamuse import app, safety
from metamuse import models as m


@pytest.mark.parametrize(
    ("code", "message", "next_action"),
    [
        (
            m.ErrorCode.INPUT_INVALID,
            "Muse text contains credential values",
            "Remove credentials from the topic or question",
        ),
        (
            m.ErrorCode.REFERENCE_INVALID,
            "Packet is stale; prepare a new packet",
            "Prepare a new packet and send request with a new exact share grant",
        ),
        (
            m.ErrorCode.SCOPE_DENIED,
            "Permission does not cover this action",
            "Request permission for this exact action",
        ),
    ],
    ids=["credential", "stale-packet", "scope"],
)
def test_applicable_preview_refusal_keeps_fixed_error_without_side_effects(
    ctx, monkeypatch, caplog, code, message, next_action
):
    app.register_builtin_operations()
    marker = f"{'PREVIEW'}_SYNTHETIC_MARKER_357913"
    request = m.MuseOpenRequest(
        project_id="missing", topic=f"password={marker}", request_key="applicable-refusal"
    )
    refusal = m.CoreError(code, message, next_action=next_action)
    calls = []

    def refuse(context, operation, body):
        assert context is ctx and operation == "muse.open"
        assert body == request.model_dump(mode="python")
        calls.append(operation)
        raise refusal

    def no_write(*args, **kwargs):
        pytest.fail("A refused approval callback must not open a write transaction")

    monkeypatch.setattr(safety, "approval_preview", refuse)
    monkeypatch.setattr(ctx.store, "transaction", no_write)
    result = safety.approval_failure(
        ctx, request, m.CoreError(m.ErrorCode.SCOPE_REQUIRED, "A permit is required")
    )
    assert calls == ["muse.open"]
    assert result.status == "error" and result.error == refusal.detail
    assert result.approval is None
    assert marker not in result.model_dump_json() + caplog.text
    assert "Approval preview callback failed" not in caplog.text
    with ctx.store.connection() as conn:
        for table in ("jobs", "exchanges", "discussions", "events", "request_receipts", "permits"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
