"""F4 receipt ordering also covers the muse-open reconciliation read path."""

import pytest
from test_muse_lifecycle import fixture_ctx as fixture_ctx
from test_muse_lifecycle import grant, invoke, run

from metamuse import _muse_lifecycle as lifecycle
from metamuse import models as m
from metamuse import safety


def test_open_reconciliation_replays_before_upgraded_topic_check(fixture_ctx, project, monkeypatch):
    ctx = fixture_ctx
    marker = f"{ctx._frontend}_MARKER_LEGACY_TOPIC"
    opening = m.MuseOpenRequest(
        project_id=project, topic=f"hatch_sess={marker}", request_key="legacy-open"
    )
    with monkeypatch.context() as old_rules:
        old_rules.setattr(lifecycle, "_check_question", lambda text: None)
        prepared = lifecycle.prepare_open(ctx, opening)
        opening.access_grant_id = grant(
            ctx, project, safety.action_digest(prepared.target, prepared.payload)
        )
        accepted = invoke(ctx, "open", opening)
        run(ctx)
        discussion = ctx.store.get(m.Discussion, accepted.data["discussion_id"])
        target, payload = lifecycle.read_action(discussion)
        request = opening.model_copy(
            update={
                "discussion_id": discussion.id,
                "request_key": "legacy-reconcile",
                "access_grant_id": grant(
                    ctx, project, safety.action_digest(target, payload), operation="muse.read"
                ),
            }
        )
        original = invoke(ctx, "open", request)
    assert original.status == "accepted"
    with monkeypatch.context() as read_only:
        read_only.setattr(
            ctx.store, "transaction", lambda: pytest.fail("Reconciliation replay writes")
        )
        replay = invoke(ctx, "open", request)
    assert replay.status == "accepted" and replay.data["job_id"] == original.data["job_id"]
    changed = request.model_copy(update={"topic": "Different topic"})
    assert invoke(ctx, "open", changed).error.code == m.ErrorCode.IDEMPOTENCY_CONFLICT
