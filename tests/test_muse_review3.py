"""Offline regressions for the independent B10 lifecycle review."""

import copy
import sqlite3

import pytest
from test_muse_lifecycle import fixture_ctx as fixture_ctx
from test_muse_lifecycle import grant, invoke, opened, packet, run

from metamuse import app, jobs, safety
from metamuse import models as m
from metamuse._muse_lifecycle import prepare_open, prepare_send, read_action


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


def queue_send(ctx, project, discussion_id, pkt, key, question="Same host question"):
    request = m.MuseSendRequest(
        discussion_id=discussion_id,
        packet_id=pkt.id,
        question=question,
        share_grant_id="placeholder",
        request_key=key,
    )
    prepared = prepare_send(ctx, request)
    request.share_grant_id = grant(
        ctx,
        project,
        safety.action_digest(prepared.target, prepared.payload),
        kind="share",
        packet=pkt,
    )
    response = invoke(ctx, "send", request)
    assert response.status == "accepted"
    return response.data["exchange_id"]


def queue_history(ctx, exchange_id):
    exchange = ctx.store.get(m.Exchange, exchange_id)
    discussion = ctx.store.get(m.Discussion, exchange.discussion_id)
    target, payload = read_action(discussion)
    permit = grant(
        ctx, discussion.project_id, safety.action_digest(target, payload), operation="muse.read"
    )
    response = invoke(
        ctx,
        "read",
        {
            "exchange_id": exchange_id,
            "refresh": True,
            "access_grant_id": permit,
        },
    )
    assert response.status == "accepted"


def markerless_history(ctx, discussion_id):
    session = ctx.store.get(m.Discussion, discussion_id).remote_session
    events = copy.deepcopy(ctx.muse_gateway.events[session])
    for event in events:
        event.pop("request_marker", None)
    ctx.muse_gateway.pages = [events]


def test_never_sent_exchange_cannot_steal_markerless_retry(fixture_ctx, project):
    ctx = fixture_ctx
    opening, _ = opened(ctx, project)
    did = opening.data["discussion_id"]
    pkt = packet(ctx, project)
    ctx.muse_gateway.mode = "not_submitted"
    first = queue_send(ctx, project, did, pkt, "first")
    run(ctx)
    assert (
        invoke(
            ctx,
            "close_exchange",
            {"exchange_id": first, "outcome": "abandoned", "request_key": "close-first"},
        ).status
        == "ok"
    )
    ctx.muse_gateway.mode = "normal"
    second = queue_send(ctx, project, did, pkt, "second")
    run(ctx)
    markerless_history(ctx, did)
    queue_history(ctx, second)
    run(ctx)
    assert invoke(ctx, "read", {"exchange_id": first}).data["messages"] == []
    result = invoke(ctx, "read", {"exchange_id": second})
    assert len(result.data["messages"]) == 2 and result.data["association"] == "progress"


def test_prepared_exchange_cannot_borrow_history_or_lose_its_ack(fixture_ctx, project):
    ctx = fixture_ctx
    opening, _ = opened(ctx, project)
    did = opening.data["discussion_id"]
    pkt = packet(ctx, project)
    first = queue_send(ctx, project, did, pkt, "first")
    run(ctx)
    assert (
        invoke(
            ctx,
            "close_exchange",
            {"exchange_id": first, "outcome": "needs_more_data", "request_key": "close-first"},
        ).status
        == "ok"
    )
    markerless_history(ctx, did)
    queue_history(ctx, first)
    second = queue_send(ctx, project, did, pkt, "second")
    run(ctx)
    exchange = ctx.store.get(m.Exchange, second)
    assert exchange.state == "prepared" and exchange.delivery_proof is None
    with ctx.store.connection() as conn:
        observation = conn.execute(
            "SELECT id FROM muse_observations WHERE json_extract(raw_event,'$.event_name')="
            "'message.user' LIMIT 1"
        ).fetchone()[0]
        proof = m.MuseProof(
            kind="user_echo",
            step_id=exchange.submission_step_id,
            outbound_digest=exchange.outbound_digest,
            observation_id=observation,
        )
        with pytest.raises(sqlite3.IntegrityError, match="delivery proof must reference"):
            conn.execute(
                "UPDATE exchanges SET delivery_proof=? WHERE id=?",
                (
                    m.canonical_json(proof).decode(),
                    second,
                ),
            )
    run(ctx)
    exchange = ctx.store.get(m.Exchange, second)
    assert exchange.bytes_handed_off is True and exchange.attempt_verdict == "acked"
    assert exchange.delivery_proof.kind == "ack"
    assert ctx.store.get(m.JobStep, exchange.submission_step_id).state == "succeeded"
    assert sum(route == "chat.stream" for route, _ in ctx.muse_gateway.calls) == 2


def test_identical_delivered_turns_without_markers_stay_ambiguous(fixture_ctx, project):
    ctx = fixture_ctx
    opening, _ = opened(ctx, project)
    did = opening.data["discussion_id"]
    pkt = packet(ctx, project)
    first = queue_send(ctx, project, did, pkt, "first")
    run(ctx)
    invoke(
        ctx,
        "close_exchange",
        {"exchange_id": first, "outcome": "needs_more_data", "request_key": "close-first"},
    )
    second = queue_send(ctx, project, did, pkt, "second")
    run(ctx)
    markerless_history(ctx, did)
    queue_history(ctx, second)
    run(ctx)
    for identity in (first, second):
        data = invoke(ctx, "read", {"exchange_id": identity}).data
        assert data["association"] == "ambiguous" and data["messages"] == []


def test_history_proof_before_transport_ack_preserves_both(fixture_ctx, project):
    from metamuse._muse_lifecycle import _associate, _normalize, _observe

    ctx = fixture_ctx
    opening, _ = opened(ctx, project)
    did = opening.data["discussion_id"]
    original = ctx.muse_gateway.chat_stream

    def send(prepared, *, authorization, **kwargs):
        result = original(prepared, authorization=authorization, **kwargs)
        step = authorization._issuer._tokens[authorization][0]
        exchange = ctx.store.get(m.Exchange, step.target["intent_id"])
        discussion = ctx.store.get(m.Discussion, did)
        events = ctx.muse_gateway.events[discussion.remote_session]
        _observe(step, discussion, _normalize(events, discussion.remote_session))
        _associate(step, discussion, exchange)
        assert ctx.store.get(m.Exchange, exchange.id).delivery_proof.kind == "user_echo"
        return result

    ctx.muse_gateway.chat_stream = send
    identity = queue_send(ctx, project, did, packet(ctx, project), "send")
    run(ctx)
    exchange = ctx.store.get(m.Exchange, identity)
    assert exchange.attempt_verdict == "acked" and exchange.bytes_handed_off is True
    assert exchange.delivery_proof.kind == "user_echo"
    assert ctx.store.get(m.JobStep, exchange.submission_step_id).state == "succeeded"


@pytest.mark.parametrize("race", ["close", "cancel", "write_failure", "settlement_failure"])
def test_zero_handoff_races_do_not_wedge_worker(fixture_ctx, project, monkeypatch, race):
    ctx = fixture_ctx
    opening, _ = opened(ctx, project)
    did = opening.data["discussion_id"]
    original = ctx.muse_gateway.chat_stream

    def send(prepared, *, authorization, **kwargs):
        result = original(prepared, authorization=authorization, **kwargs)
        step = authorization._issuer._tokens[authorization][0]
        if race == "close":
            closed = app.close_muse_exchange(
                ctx,
                {
                    "exchange_id": step.target["intent_id"],
                    "outcome": "abandoned",
                    "request_key": "close-race",
                },
            )
            assert closed.status == "ok"
        elif race == "cancel":
            assert (
                app.control_job(
                    ctx,
                    {
                        "job_id": step.job.id,
                        "operation": "cancel",
                        "request_key": "cancel-race",
                    },
                ).status
                == "ok"
            )
        return result

    ctx.muse_gateway.chat_stream = send
    ctx.muse_gateway.mode = "not_submitted"
    identity = queue_send(ctx, project, did, packet(ctx, project), "send")
    registry = dict(jobs.OPERATIONS)

    def later(step):
        step.executor.request_stop()
        return m.JobResult(coverage_status="bounded", data={"later": True})

    jobs.register_operation("review.later", later, "none", registry=registry)
    queued = jobs.submit_job(
        ctx.store, "review.later", "later", {}, project_id=project, registry=registry
    )
    assert queued.status == "accepted"
    if race == "write_failure":

        def fail(*args, **kwargs):
            raise m.CoreError(m.ErrorCode.STORAGE, "Synthetic accounting failure")

        monkeypatch.setattr(jobs.StepContext, "commit_not_submitted", fail)
    elif race == "settlement_failure":

        def fail(*args, **kwargs):
            raise m.CoreError(m.ErrorCode.STORAGE, "Synthetic settlement failure")

        monkeypatch.setattr(jobs.Executor, "settle_uncertain", fail)
    with jobs.Executor(ctx.store, registry=registry, muse_gateway=ctx.muse_gateway) as executor:
        assert executor.run() == 2
        assert not executor._tokens and not executor._zero_handoff and not executor._proofs
    assert ctx.store.get(m.Job, queued.data["job_id"]).state == "succeeded"
    exchange = ctx.store.get(m.Exchange, identity)
    step = ctx.store.get(m.JobStep, exchange.submission_step_id)
    job = ctx.store.get(m.Job, step.job_id)
    if race in ("write_failure", "settlement_failure"):
        assert step.state == "send_uncertain" and job.remote_state == "unknown"
        with ctx.store.connection() as conn:
            assert conn.execute(
                "SELECT 1 FROM events WHERE kind='step.not_submitted_save_failed' AND object_id=?",
                (step.id,),
            ).fetchone()
        if race == "write_failure":
            assert exchange.bytes_handed_off is None and exchange.delivery_proof is None
    else:
        assert step.state == "failed" and job.remote_state == "not_submitted"
        assert (
            exchange.bytes_handed_off is False and exchange.delivery_proof.kind == "not_submitted"
        )
        assert step.checkpoint["not_submitted"] is True
        assert ctx.store.get(m.Discussion, did).quarantine_step_id is None
        if race == "close":
            assert exchange.state == "closed" and exchange.outcome == "abandoned"
        else:
            assert exchange.state == "failed_before_send" and job.state == "cancelled"
    assert sum(route == "chat.stream" for route, _ in ctx.muse_gateway.calls) == 1
