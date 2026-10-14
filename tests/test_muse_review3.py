"""Offline regressions for the independent B10 lifecycle review."""

from test_muse_lifecycle import fixture_ctx as fixture_ctx
from test_muse_lifecycle import grant, invoke, run

from metamuse import models as m
from metamuse import safety
from metamuse._muse_lifecycle import prepare_open


def queue_open(ctx, project, key):
    request = m.MuseOpenRequest(
        project_id=project, topic="Same host topic", access_grant_id="placeholder", request_key=key
    )
    prepared = prepare_open(ctx, request)
    request.access_grant_id = grant(
        ctx, project, safety.action_digest(prepared.target, prepared.payload)
    )
    response = invoke(ctx, "open", request)
    assert response.status == "accepted"
    return response, request


def test_queued_open_rechecks_unresolved_creation_before_reserving(fixture_ctx, project):
    ctx = fixture_ctx
    first, _ = queue_open(ctx, project, "first")
    second, request = queue_open(ctx, project, "second")
    ctx.muse_gateway.mode = "open_timeout"
    run(ctx)
    assert ctx.store.get(m.Job, first.data["job_id"]).remote_state == "unknown"
    ctx.muse_gateway.mode = "normal"
    run(ctx)
    discussion = ctx.store.get(m.Discussion, second.data["discussion_id"])
    step = ctx.store.get(m.JobStep, discussion.open_step_id)
    job = ctx.store.get(m.Job, step.job_id)
    assert step.state == "failed" and job.remote_state == "not_submitted"
    assert discussion.open_submitted_at_ms is None and discussion.remote_session is None
    assert ctx.store.get(m.Permit, request.access_grant_id).used == 0
    assert sum(route == "session.start" for route, _ in ctx.muse_gateway.calls) == 1
