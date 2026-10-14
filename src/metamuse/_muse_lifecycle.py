"""Durable host-led Muse lifecycle, with separate submission and recovery jobs."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from datetime import datetime

from metamuse import models as m
from metamuse._muse_adapters import BoundMuseCredentials, MuseURLGuard, resolve_all
from metamuse.config import load_config
from metamuse.jobs import OPERATIONS, UNCERTAIN, StepSpec, register_operation, submit_job
from metamuse.muse import (
    MUSE_ORIGIN,
    Association,
    Exchange,
    MuseNotSubmitted,
    MuseProtocolStop,
    MuseStateUnknown,
    MuseTransport,
    ObservationMemory,
    associate_reply,
    normalize_event,
    prepare_muse_request,
    user_echo,
)
from metamuse.safety import (
    PermitUse,
    action_digest,
    external_action_verifier,
    redact_text,
    verify_permit,
)
from metamuse.store import CONTENT_CURSOR_KEY

TOOLS = frozenset(
    {
        "metamuse_muse_open",
        "metamuse_muse_send",
        "metamuse_muse_read",
        "metamuse_muse_close_exchange",
    }
)
MAX_OBSERVATIONS = 2000
MAX_HYDRATION_BYTES = 2 * 1024 * 1024
READ_PAGE = 50


def _error(code, message, action=None):
    raise m.CoreError(code, message, next_action=action)


def _binding(ctx, discussion=None):
    policy = ctx.policy.muse
    injected_fixture = getattr(ctx.muse_gateway, "backend", None) == "fixture"
    if not policy.enabled and not injected_fixture:
        _error(
            m.ErrorCode.SCOPE_REQUIRED,
            "Muse is disabled",
            "Enable Muse explicitly; configure a binding or select the fixture backend",
        )
    if discussion is None:
        if policy.backend == "fixture" or injected_fixture:
            return m.MuseBinding(
                provider="fixture", account="fixture", auth_ref="fixture"
            ), "fixture"
        if policy.binding is None:
            _error(
                m.ErrorCode.AUTH_REQUIRED,
                "Muse binding is missing",
                "Configure muse.binding provider, account and auth_ref",
            )
        binding, backend = policy.binding, "live"
    else:
        if discussion.backend is None or discussion.account is None:
            _error(
                m.ErrorCode.AUTH_REQUIRED,
                "Legacy discussion has no binding",
                "Open a new explicitly bound discussion",
            )
        binding = m.MuseBinding(
            provider=discussion.provider, account=discussion.account, auth_ref=discussion.auth_ref
        )
        backend = discussion.backend
        if backend != policy.backend and ctx.muse_gateway is None:
            _error(
                m.ErrorCode.SCOPE_DENIED, "Discussion backend differs from operator configuration"
            )
    if backend == "live":
        BoundMuseCredentials(binding, ctx.secret_store).cookies(binding.auth_ref)
        if ctx.policy.app.network_mode != "live":
            _error(m.ErrorCode.SCOPE_DENIED, "External operations are disabled in offline mode")
    return binding, backend


def capability_status(ctx):
    fixture = (
        ctx.policy.muse.backend == "fixture"
        or getattr(ctx.muse_gateway, "backend", None) == "fixture"
    )
    environment = "ready"
    if not ctx.policy.muse.enabled and not fixture:
        environment = "unavailable"
    elif not fixture:
        if ctx.policy.muse.binding is None:
            environment = "needs_auth"
        else:
            try:
                BoundMuseCredentials(ctx.policy.muse.binding, ctx.secret_store).cookies(
                    ctx.policy.muse.binding.auth_ref
                )
            except m.CoreError:
                environment = "needs_auth"
            if environment == "ready" and ctx.policy.app.network_mode != "live":
                environment = "unavailable"
    return {
        name: {
            "verification": "fixture_passed" if fixture else "unverified",
            "environment": "ready"
            if name in ("metamuse_muse_read", "metamuse_muse_close_exchange")
            else environment,
            "permission": "granted_scope"
            if name in ("metamuse_muse_read", "metamuse_muse_close_exchange")
            else "not_configured",
        }
        for name in TOOLS
    }


def _identity(kind, project, request_key):
    return kind + "_" + m.payload_digest({"project": project, "request_key": request_key})[:32]


def _target(project, binding, backend, operation, *, intent=None, session=None, packet=None):
    target = {
        "project_id": project,
        "provider": binding.provider,
        "account": binding.account,
        "backend": backend,
        "operation": operation,
        "origin": MUSE_ORIGIN,
    }
    if intent is not None:
        target["intent_id"] = intent
    if session is not None:
        target["session_id"] = session
    if packet is not None:
        target["packet_id"] = packet
    return target


def _prepared(route, target, body, marker, node):
    path = "/session/start" if route == "session.start" else "/chat/stream"
    headers = {"x-request-id": marker, "content-type": "application/json"}
    payload = {"verb": "POST", "path": path, "headers": headers, "body": body, "node_id": node}
    return m.MusePayload(
        route=route,
        path=path,
        marker=marker,
        node_id=node,
        headers=headers,
        body=body,
        target=target,
        payload=payload,
    )


def prepare_open(ctx, request: m.MuseOpenRequest):
    """Operator preview helper: full stable target/payload digest for an access grant."""
    binding, backend = _binding(ctx)
    identity = _identity("dsc", request.project_id, request.request_key)
    target = _target(request.project_id, binding, backend, "muse.open", intent=identity)
    node = request.project_id
    marker = "open-" + identity[4:]
    return _prepared(
        "session.start", target, {"title": request.topic, "node_id": node}, marker, node
    )


def prepare_send(ctx, request: m.MuseSendRequest):
    discussion = ctx.store.get(m.Discussion, request.discussion_id)
    binding, backend = _binding(ctx, discussion)
    packet = ctx.store.get(m.Packet, request.packet_id)
    if packet.project_id != discussion.project_id:
        _error(m.ErrorCode.REFERENCE_INVALID, "Packet is outside this discussion project")
    if not discussion.remote_session:
        _error(m.ErrorCode.EXTERNAL_STATE_UNKNOWN, "Session mapping requires reconciliation")
    for value, cap in (
        (request.question, ctx.limits.question_max_utf8_bytes),
        (packet.rendered_text, ctx.limits.packet_max_utf8_bytes),
    ):
        if len(value.encode("utf-8")) > cap:
            _error(m.ErrorCode.LIMIT_REACHED, "Muse text exceeds its UTF-8 limit")
    _history_baseline(ctx.store, discussion)
    identity = _identity("exc", discussion.id, request.request_key)
    target = _target(
        discussion.project_id,
        binding,
        backend,
        "muse.send",
        intent=identity,
        session=discussion.remote_session,
        packet=packet.id,
    )
    # No generated question or persona. A documented separator joins the two exact host texts.
    body = {
        "session_id": discussion.remote_session,
        "text": request.question + "\n\n" + packet.rendered_text,
        "node_id": discussion.project_id,
    }
    prepared = _prepared("chat.stream", target, body, "send-" + identity[4:], discussion.project_id)
    return prepared.model_copy(
        update={
            "payload": {
                **prepared.payload,
                "question": request.question,
                "packet_text": packet.rendered_text,
            }
        }
    )


def _history_baseline(store, discussion):
    with store.connection() as conn:
        row = conn.execute(
            "SELECT e.job_id FROM events e JOIN jobs j ON j.id=e.job_id "
            "WHERE e.kind='muse.history_observed' AND e.object_id=? "
            "AND j.operation='muse.history' AND j.state IN ('succeeded','partial') "
            "AND json_extract(e.detail,'$.session_id')=? ORDER BY e.rowid DESC LIMIT 1",
            (discussion.id, discussion.remote_session),
        ).fetchone()
    if row is None and discussion.backend != "fixture":
        _error(
            m.ErrorCode.EXTERNAL_STATE_UNKNOWN,
            "An authorized history baseline is required",
            "Call muse_open with the original discussion_id and a current muse.read access "
            "grant before preparing a send",
        )
    return {
        "seq": discussion.last_seq,
        "session_id": discussion.remote_session,
        "history_job_id": row[0] if row else None,
        "basis": "stored_history" if row else "fixture_new_session",
    }


def read_action(discussion, *, sessions=False):
    """Operator preview helper for a current access grant; no IO."""
    binding = m.MuseBinding(
        provider=discussion.provider, account=discussion.account, auth_ref=discussion.auth_ref
    )
    target = _target(
        discussion.project_id,
        binding,
        discussion.backend,
        "muse.read",
        session=discussion.remote_session,
    )
    target["discussion_id"] = discussion.id
    payload = (
        {"route": "sessions.list"}
        if sessions
        else {"route": "chat.history", "session_id": discussion.remote_session, "limit": 200}
    )
    return target, payload


def _wire(payload):
    expected = {
        "verb": payload.verb,
        "path": payload.path,
        "headers": payload.headers,
        "body": payload.body,
        "node_id": payload.node_id,
    }
    if any(payload.payload.get(key) != value for key, value in expected.items()):
        _error(m.ErrorCode.PROTOCOL_MISMATCH, "Prepared wire bytes differ from verified payload")
    return prepare_muse_request(
        payload.route,
        marker=payload.marker,
        body=payload.body,
        target=payload.target,
        payload=payload.payload,
    )


def _use(discussion, operation, digest, packet=None):
    return PermitUse(
        kind="share" if operation == "muse.send" else "access",
        project_id=discussion.project_id,
        provider=discussion.provider,
        account=discussion.account,
        operation=operation,
        digest=digest,
        packet_id=packet,
    )


def _share_target(permit, digest):
    if permit.exact_digest != digest:
        _error(
            m.ErrorCode.SCOPE_DENIED,
            "Muse share grant must bind the exact question, packet and target",
            "Review the prepared action digest and issue an exact share grant",
        )


def _replay(ctx, operation, request):
    with ctx.store.connection() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE operation=? AND request_key=?",
            (operation, request.request_key),
        ).fetchone()
        if row is None:
            return None
        job = ctx.store.decode(m.Job, row)
        if job.payload != request.model_dump(mode="json"):
            _error(m.ErrorCode.IDEMPOTENCY_CONFLICT, "Request key has a different payload")
        step = conn.execute(
            "SELECT target FROM job_steps WHERE job_id=? ORDER BY sequence LIMIT 1", (job.id,)
        ).fetchone()
        target = json.loads(step[0])
        discussion_id = (
            target["intent_id"]
            if operation == "muse.open"
            else ctx.store.get(m.Exchange, target["intent_id"], conn=conn).discussion_id
        )
    result = submit_job(
        ctx.store,
        operation,
        request.request_key,
        job.payload,
        project_id=job.project_id,
        steps=[StepSpec(target=target)],
    )
    if result.data is not None:
        result.data.update(
            {
                "discussion_id": discussion_id,
                "exchange_id": target.get("intent_id") if operation == "muse.send" else None,
                "backend": target["backend"],
                "mock": target["backend"] == "fixture",
            }
        )
    return result


def _assert_available(conn, discussion_id, *, exclude=None):
    if (
        conn.execute(
            "SELECT 1 FROM discussions WHERE id=? AND quarantine_step_id IS NOT NULL",
            (discussion_id,),
        ).fetchone()
        or conn.execute(
            (
                "SELECT 1 FROM exchanges e LEFT JOIN job_steps s ON s.id=e.submission_s"
                "tep_id WHERE e.discussion_id=? AND e.id IS NOT ? AND (e.outcome IS NUL"
                "L OR s.state IN ('submitting','open_uncertain','send_uncertain','needs"
                "_reconcile')) LIMIT 1"
            ),
            (discussion_id, exclude),
        ).fetchone()
    ):
        _error(
            m.ErrorCode.EXTERNAL_STATE_UNKNOWN,
            "Earlier exchange or delivery remains unresolved",
            "Read and reconcile the original exchange, then close its host outcome",
        )


def _assert_open_available(db, discussion):
    if db.execute(
        "SELECT 1 FROM discussions d LEFT JOIN job_steps s ON s.id=d.open_step_id "
        "WHERE d.project_id=? AND d.provider=? AND d.account=? AND d.id<>? AND "
        "(d.open_attempt_state IN ('submitting','open_uncertain') OR "
        "s.state IN ('submitting','open_uncertain','needs_reconcile')) LIMIT 1",
        (discussion.project_id, discussion.provider, discussion.account, discussion.id),
    ).fetchone():
        _error(
            m.ErrorCode.EXTERNAL_STATE_UNKNOWN,
            "Earlier session creation remains unresolved",
            "Reconcile the original discussion; a new key is not proof of zero delivery",
        )


def open_muse_discussion(ctx, request):
    register_operations()
    if request.discussion_id is not None:
        discussion = ctx.store.get(m.Discussion, request.discussion_id)
        _binding(ctx, discussion)
        if discussion.project_id != request.project_id or discussion.topic != request.topic:
            _error(
                m.ErrorCode.REFERENCE_INVALID, "Reconciliation must name the original discussion"
            )
        return _queue_read(
            ctx,
            discussion,
            request.access_grant_id,
            sessions=discussion.remote_session is None,
            request_key=request.request_key,
        )
    replay = _replay(ctx, "muse.open", request)
    if replay is not None:
        return replay
    prepared = prepare_open(ctx, request)
    binding, backend = _binding(ctx)
    discussion = m.Discussion(
        id=prepared.target["intent_id"],
        project_id=request.project_id,
        provider=binding.provider,
        account=binding.account,
        auth_ref=binding.auth_ref,
        topic=request.topic,
        backend=backend,
        open_request_key=request.request_key,
        open_payload=prepared,
        open_permit_id=request.access_grant_id,
    )
    with ctx.store.connection() as conn:
        ctx.store.get(m.Project, request.project_id, conn=conn)
        verify_permit(
            ctx.store,
            request.access_grant_id,
            _use(discussion, "muse.open", action_digest(prepared.target, prepared.payload)),
            conn=conn,
        )

    with ctx.store.connection() as conn:
        _assert_open_available(conn, discussion)

    def domain(writer, job, steps):
        _assert_open_available(writer, discussion)
        writer.insert(discussion)
        writer.execute(
            "UPDATE discussions SET open_step_id=? WHERE id=?", (steps[0].id, discussion.id)
        )

    result = submit_job(
        ctx.store,
        "muse.open",
        request.request_key,
        request.model_dump(mode="json"),
        project_id=request.project_id,
        steps=[StepSpec(target=prepared.target)],
        prepare_domain=domain,
    )
    if result.data is not None:
        result.data.update(discussion_id=discussion.id, backend=backend, mock=backend == "fixture")
    return result


def submit_muse_exchange(ctx, request):
    register_operations()
    replay = _replay(ctx, "muse.send", request)
    if replay is not None:
        return replay
    prepared = prepare_send(ctx, request)
    discussion = ctx.store.get(m.Discussion, request.discussion_id)
    packet = ctx.store.get(m.Packet, request.packet_id)
    exchange = m.Exchange(
        id=prepared.target["intent_id"],
        project_id=discussion.project_id,
        discussion_id=discussion.id,
        request_key=request.request_key,
        question=request.question,
        packet_id=packet.id,
        digest=m.payload_digest(request.model_dump(mode="json")),
        permit_id=request.share_grant_id,
        baseline=_history_baseline(ctx.store, discussion),
        request_marker=prepared.marker,
        outbound_payload=prepared,
        outbound_digest=hashlib.sha256(m.canonical_json(prepared.body)).hexdigest(),
    )
    with ctx.store.connection() as conn:
        _assert_available(conn, discussion.id)
        permit = verify_permit(
            ctx.store,
            request.share_grant_id,
            _use(
                discussion, "muse.send", action_digest(prepared.target, prepared.payload), packet.id
            ),
            conn=conn,
        )
        _share_target(permit, action_digest(prepared.target, prepared.payload))
        count = conn.execute(
            "SELECT count(*) FROM exchanges WHERE discussion_id=?", (discussion.id,)
        ).fetchone()[0]
        if count >= ctx.limits.max_exchanges:
            _error(m.ErrorCode.LIMIT_REACHED, "Discussion exchange limit reached")

    def domain(writer, job, steps):
        _assert_available(writer, discussion.id)
        writer.insert(
            exchange.model_copy(update={"job_id": job.id, "submission_step_id": steps[0].id})
        )

    result = submit_job(
        ctx.store,
        "muse.send",
        request.request_key,
        request.model_dump(mode="json"),
        project_id=discussion.project_id,
        steps=[StepSpec(target=prepared.target)],
        prepare_domain=domain,
    )
    if result.data is not None:
        result.data.update(
            discussion_id=discussion.id,
            exchange_id=exchange.id,
            backend=discussion.backend,
            mock=discussion.backend == "fixture",
        )
    return result


def _gateway(step, discussion):
    policy = load_config(step.store.paths.root)
    if (
        not policy.muse.enabled
        and getattr(step.executor.muse_gateway, "backend", None) != "fixture"
    ):
        _error(m.ErrorCode.SCOPE_REQUIRED, "Muse is disabled")
    if discussion.backend == "fixture":
        if policy.muse.backend != "fixture" and step.executor.muse_gateway is None:
            _error(m.ErrorCode.SCOPE_DENIED, "Fixture backend was not selected")
        if step.executor.muse_gateway is None:
            from metamuse._muse_fixture import OfflineMuseGateway

            step.executor.muse_gateway = OfflineMuseGateway(step.store)
        if getattr(step.executor.muse_gateway, "backend", None) != "fixture":
            _error(m.ErrorCode.SCOPE_DENIED, "Fixture gateway must carry its backend label")
        return step.executor.muse_gateway
    binding = m.MuseBinding(
        provider=discussion.provider, account=discussion.account, auth_ref=discussion.auth_ref
    )
    return MuseTransport(
        network_mode=policy.app.network_mode,
        auth_ref=discussion.auth_ref,
        credentials=BoundMuseCredentials(binding, getattr(step.executor, "secret_store", None)),
        guard=MuseURLGuard(getattr(step.executor, "muse_resolver", None) or resolve_all),
    )


def _millis(clock):
    return int(datetime.fromisoformat(clock()).timestamp() * 1000)


def _exchange_view(store, discussion, exchange, conn=None):
    from metamuse._muse_redact import sanitize_document

    def unresolved(db):
        return bool(
            db.execute(
                (
                    "SELECT 1 FROM exchanges e LEFT JOIN job_steps s ON s.id=e.submission_s"
                    "tep_id WHERE e.discussion_id=? AND e.rowid<(SELECT rowid FROM exchanges "
                    "WHERE id=?) AND (e.outcome IS NULL OR s"
                    ".state IN ('submitting','send_uncertain','open_uncertain','needs_recon"
                    "cile')) LIMIT 1"
                ),
                (discussion.id, exchange.id),
            ).fetchone()
        )

    if conn is None:
        with store.connection() as db:
            earlier = unresolved(db)
    else:
        earlier = unresolved(conn)
    return Exchange(
        discussion.remote_session,
        exchange.baseline.get("seq", 0),
        sanitize_document(exchange.outbound_payload.body["text"]),
        exchange.request_marker,
        exchange.submitted_at_ms,
        earlier,
    )


def _mutate(step):
    is_open = step.job.operation == "muse.open"
    if is_open:
        discussion = step.store.get(m.Discussion, step.target["intent_id"])
        saved = discussion.open_payload
        permit_id = discussion.open_permit_id
        exchange = None
    else:
        exchange = step.store.get(m.Exchange, step.target["intent_id"])
        discussion = step.store.get(m.Discussion, exchange.discussion_id)
        saved = exchange.outbound_payload
        permit_id = exchange.permit_id
    if (
        saved is None
        or saved.target != step.target
        or (exchange is not None and exchange.state != "prepared")
    ):
        _error(m.ErrorCode.EXTERNAL_STATE_UNKNOWN, "Original attempt is not prepared")
    wire = _wire(saved)
    use = _use(
        discussion,
        step.job.operation,
        action_digest(saved.target, saved.payload),
        exchange.packet_id if exchange else None,
    )
    base = external_action_verifier(
        step,
        permit_id=permit_id,
        use=use,
        load_payload=lambda conn, target: (
            (
                step.store.get(m.Discussion, discussion.id, conn=conn).open_payload
                if is_open
                else step.store.get(m.Exchange, exchange.id, conn=conn).outbound_payload
            ).payload
        ),
    )

    def verify(conn, target):
        if is_open:
            _assert_open_available(conn, discussion)
        else:
            _assert_available(conn, discussion.id, exclude=exchange.id)
            permit = step.store.get(m.Permit, permit_id, conn=conn)
            _share_target(permit, use.digest)
        digest = base(conn, target)
        if is_open:
            conn.execute(
                (
                    "UPDATE discussions SET open_attempt_state='submitting',open_submitted_"
                    "at_ms=? WHERE id=?"
                ),
                (_millis(step.store.clock), discussion.id),
            )
        else:
            high_water = conn.execute(
                "SELECT COALESCE(MAX(rowid),0) FROM muse_observations"
            ).fetchone()[0]
            conn.execute(
                "UPDATE exchanges SET state='submitting',submitted_at_ms=?,"
                "submitted_observation_high_water=? WHERE id=?",
                (_millis(step.store.clock), high_water, exchange.id),
            )
        return digest

    gateway = _gateway(step, discussion)
    token = step.before_external_action(step.target, verify=verify)
    result = None
    failure = None
    try:
        result = (
            gateway.session_start(wire, authorization=token)
            if is_open
            else gateway.chat_stream(
                wire,
                authorization=token,
                exchange=_exchange_view(
                    step.store, discussion, step.store.get(m.Exchange, exchange.id)
                ),
            )
        )
    except (MuseStateUnknown, MuseProtocolStop, MuseNotSubmitted) as exc:
        failure = exc
    finally:
        if discussion.backend == "live":
            gateway.close()
    if is_open:
        _save_open(step, discussion, result, failure)
    else:
        _save_send(step, discussion, exchange, result, failure)
    return m.JobResult(
        coverage_status="bounded",
        stop_reason="submission_acknowledged",
        data={
            "discussion_id": discussion.id,
            "exchange_id": exchange.id if exchange else None,
            "backend": discussion.backend,
            "mock": discussion.backend == "fixture",
            "research_complete": False,
        },
    )


def _save_open(step, discussion, result, failure):
    handed = result.bytes_handed_off if result is not None else failure.bytes_handed_off
    zero = handed is False
    if zero:
        zero_proof = step.executor.record_not_submitted(step, failure)
    verdict = "undecided"
    session = None
    if result is not None:
        from metamuse.muse import attempt_evidence

        verdict = attempt_evidence(
            stream_complete=True, stream_status=result.status, body=result.body
        ).verdict
        try:
            value = json.loads(result.body)
            session = value.get("session_id")
            if (
                not isinstance(session, str)
                or not session
                or session in ("redacted", "[REDACTED]")
                or len(session) > 256
                or any(ord(c) < 32 for c in session)
            ):
                session = None
        except (ValueError, TypeError, AttributeError):
            session = None
    reliable = verdict == "acked" and session is not None
    proof = (
        m.MuseProof(
            kind="ack" if reliable else "not_submitted",
            step_id=step.step.id,
            outbound_digest=hashlib.sha256(
                m.canonical_json(discussion.open_payload.body)
            ).hexdigest(),
        )
        if reliable or zero
        else None
    )

    def save(writer):
        writer.execute(
            (
                "UPDATE discussions SET open_attempt_state=?,open_bytes_handed_off=?,op"
                "en_verdict=?,open_error_code=?,open_stream_id=?,remote_session=?,open_"
                "delivery_proof=?,state=? WHERE id=?"
            ),
            (
                "sent" if reliable else "failed_before_send" if zero else "open_uncertain",
                int(handed),
                verdict,
                failure.detail.code.value if failure else None,
                result.stream_id if result else getattr(failure, "stream_id", None),
                session if reliable else None,
                m.canonical_json(proof.model_dump(mode="json")).decode() if proof else None,
                "open" if reliable else "opening",
                discussion.id,
            ),
        )

    if zero:
        step.commit_not_submitted(zero_proof, write_batch=save)
    else:
        step.commit_checkpoint({"not_submitted": False}, write_batch=save)
    if not reliable:
        _error(
            m.ErrorCode.NETWORK_TIMEOUT if zero else m.ErrorCode.EXTERNAL_STATE_UNKNOWN,
            "Session creation requires reconciliation"
            if not zero
            else "Session failed before frame handoff",
            (
                "Reconcile this discussion with muse_open discussion_id; never create a"
                "nother session for this attempt"
            ),
        )


def _save_send(step, discussion, exchange, result, failure):
    attempt = result if result is not None else getattr(failure, "attempt", None)
    if attempt is None:
        from metamuse.muse import AttemptEvidence, StreamAttempt

        attempt = StreamAttempt(
            exchange.outbound_digest,
            exchange.request_marker,
            getattr(failure, "stream_id", None),
            failure.bytes_handed_off,
            AttemptEvidence("undecided"),
            "failed_before_send" if failure.bytes_handed_off is False else "send_uncertain",
            error_code=failure.detail.code.value,
        )
    zero = attempt.bytes_handed_off is False
    proof = (
        m.MuseProof(
            kind="not_submitted" if zero else "ack",
            step_id=step.step.id,
            outbound_digest=exchange.outbound_digest,
        )
        if zero or attempt.evidence.verdict == "acked"
        else None
    )

    def save(writer):
        current = writer.get(m.Exchange, exchange.id)
        state = (
            current.state
            if current.state in ("closed", "reply_observed")
            else "sent"
            if current.delivery_proof is not None
            and current.delivery_proof.kind in ("ack", "user_echo")
            else attempt.state
        )
        writer.execute(
            (
                "UPDATE exchanges SET state=?,stream_id=?,bytes_handed_off=?,attempt_ve"
                "rdict=?,attempt_detail=?,attempt_error_code=?,"
                "delivery_proof=COALESCE(delivery_proof,?) WHERE i"
                "d=?"
            ),
            (
                state,
                attempt.stream_id,
                int(attempt.bytes_handed_off),
                attempt.evidence.verdict,
                redact_text(attempt.evidence.detail)[0][:512],
                attempt.error_code,
                m.canonical_json(proof.model_dump(mode="json")).decode() if proof else None,
                exchange.id,
            ),
        )

    if zero:

        def settled(writer):
            writer.execute(
                "UPDATE discussions SET quarantine_step_id=NULL "
                "WHERE id=? AND quarantine_step_id=?",
                (discussion.id, step.step.id),
            )

        zero_proof = step.executor.record_not_submitted(step, attempt, after_settlement=settled)
        step.commit_not_submitted(zero_proof, write_batch=save)
    else:
        step.commit_checkpoint({"not_submitted": False}, write_batch=save)
    current = step.store.get(m.Exchange, exchange.id)
    delivered = current.delivery_proof is not None and current.delivery_proof.kind in (
        "ack",
        "user_echo",
    )
    if attempt.state != "sent" and not delivered:
        _error(
            m.ErrorCode.NETWORK_TIMEOUT if zero else m.ErrorCode.EXTERNAL_STATE_UNKNOWN,
            "Request failed before frame handoff" if zero else "Muse send remains uncertain",
            "Read the original exchange; do not resend",
        )


def _queue_read(ctx, discussion, permit_id, *, sessions=False, request_key=None, exchange_id=None):
    _binding(ctx, discussion)
    target, payload = read_action(discussion, sessions=sessions)
    with ctx.store.connection() as conn:
        verify_permit(
            ctx.store,
            permit_id,
            _use(discussion, "muse.read", action_digest(target, payload)),
            conn=conn,
        )
    result = submit_job(
        ctx.store,
        "muse.sessions" if sessions else "muse.history",
        request_key or m.new_id("refresh"),
        {
            "discussion_id": discussion.id,
            "exchange_id": exchange_id,
            "permit_id": permit_id,
            "read_payload": payload,
        },
        project_id=discussion.project_id,
        steps=[StepSpec(target=target)],
    )
    return result


def _events(body):
    from metamuse._muse_codec import json_depth

    if len(body) > 1024 * 1024 or json_depth(body) > 32:
        _error(m.ErrorCode.PROTOCOL_MISMATCH, "Muse history exceeds JSON bounds")
    failed = False
    try:
        value = json.loads(body)
        events = value["events"]
        if not isinstance(events, list) or len(events) > 200:
            raise ValueError
    except (ValueError, TypeError, KeyError, RecursionError):
        failed = True
    if failed:
        _error(m.ErrorCode.PROTOCOL_MISMATCH, "Muse history is not a bounded event list")
    return events


def _normalize(events, session):
    from metamuse._muse_redact import sanitize_document

    clean = []
    for event in events:
        if not isinstance(event, dict):
            _error(m.ErrorCode.PROTOCOL_MISMATCH, "Muse event is invalid")
        event = normalize_event(event, session)
        seq = event.get("seq")
        name = event.get("event_name")
        payload = event.get("payload", {})
        for ident in (
            event.get("session_id"),
            name,
            event.get("message_id"),
            payload.get("message_id") if isinstance(payload, dict) else None,
        ):
            if ident is not None and (
                not isinstance(ident, str)
                or len(ident) > 256
                or any(ord(char) < 32 for char in ident)
            ):
                _error(m.ErrorCode.PROTOCOL_MISMATCH, "Muse event identity is invalid")
        if isinstance(payload, dict):
            for key in ("content", "display_text", "role", "reply_to_message_id"):
                if payload.get(key) is not None and not isinstance(payload[key], str):
                    _error(m.ErrorCode.PROTOCOL_MISMATCH, "Muse event text or role is invalid")
            expected_role = (
                {"message.user": "user", "message.assistant": "assistant"}.get(name)
                if isinstance(name, str)
                else None
            )
            if payload.get("role") not in (None, "", "user", "assistant", "system", "unknown") or (
                expected_role is not None and payload.get("role") not in (None, "", expected_role)
            ):
                _error(m.ErrorCode.PROTOCOL_MISMATCH, "Muse event role conflicts with its kind")
        if (
            type(seq) is not int
            or not 0 <= seq <= 9223372036854775807
            or not isinstance(name, str)
            or not isinstance(payload, dict)
            or not isinstance(event.get("session_id"), str)
        ):
            _error(m.ErrorCode.PROTOCOL_MISMATCH, "Muse event identity is invalid")
        for source in (event, payload):
            if "time_ms" in source and (
                type(source["time_ms"]) is not int
                or not 0 <= source["time_ms"] <= 9223372036854775807
            ):
                _error(m.ErrorCode.PROTOCOL_MISMATCH, "Muse event timestamp is invalid")
            for key in ("request_marker", "x-request-id", "client_request_id"):
                if key in source and (not isinstance(source[key], str) or len(source[key]) > 128):
                    _error(m.ErrorCode.PROTOCOL_MISMATCH, "Muse event marker is invalid")
        raw = m.canonical_json(event)
        if len(raw) > 65536:
            _error(m.ErrorCode.LIMIT_REACHED, "Muse event exceeds the observation limit")
        try:
            clean.append(sanitize_document(event))
        except ValueError:
            _error(m.ErrorCode.PROTOCOL_MISMATCH, "Muse event cannot be sanitized")
    return clean


def _event_key(event):
    payload = event.get("payload", {})
    return m.canonical_json(
        [
            event.get("session_id"),
            event.get("event_name"),
            event.get("seq"),
            event.get("message_id") or payload.get("message_id") or "",
        ]
    ).decode()


def _observe(step, discussion, events):
    def save(writer):
        for event in events:
            key = _event_key(event)
            digest = m.payload_digest(event)
            if writer.execute(
                (
                    "SELECT 1 FROM muse_observations WHERE discussion_id=? AND identity_key"
                    "=? AND observation_digest=?"
                ),
                (discussion.id, key, digest),
            ).fetchone():
                continue
            payload = event.get("payload", {})
            message_id = event.get("message_id") or payload.get("message_id") or None
            row = writer.execute(
                (
                    "SELECT id FROM messages WHERE discussion_id=? AND (identity_key=? OR ("
                    "remote_session=? AND remote_message_id=?))"
                ),
                (discussion.id, key, event["session_id"], message_id),
            ).fetchone()
            if row is None:
                message = m.Message(
                    id=m.new_id("msg"),
                    discussion_id=discussion.id,
                    remote_session=event["session_id"],
                    remote_message_id=message_id,
                    seq=event["seq"],
                    role=payload.get("role")
                    or (
                        "user"
                        if event["event_name"] == "message.user"
                        else "assistant"
                        if event["event_name"] == "message.assistant"
                        else "unknown"
                    ),
                    raw_status=str(payload.get("status"))
                    if payload.get("status") is not None
                    else None,
                    text=payload.get("display_text") or payload.get("content") or "",
                    observed_at=step.store.clock(),
                    backend=discussion.backend,
                    identity_key=key,
                )
                writer.insert(message)
                local_id = message.id
            else:
                local_id = row[0]
                writer.execute(
                    (
                        "UPDATE messages SET raw_status=COALESCE(?,raw_status),text=CASE WHEN ?"
                        "<>'' THEN ? ELSE text END WHERE id=?"
                    ),
                    (
                        str(payload["status"]) if payload.get("status") is not None else None,
                        payload.get("display_text") or payload.get("content") or "",
                        payload.get("display_text") or payload.get("content") or "",
                        local_id,
                    ),
                )
            writer.insert(
                m.MuseObservation(
                    id=m.new_id("obs"),
                    discussion_id=discussion.id,
                    message_id=local_id,
                    identity_key=key,
                    raw_event=event,
                    observation_digest=digest,
                    observed_at=step.store.clock(),
                    backend=discussion.backend,
                )
            )
            if event["session_id"] == discussion.remote_session:
                writer.execute(
                    "UPDATE discussions SET last_seq=MAX(last_seq,?) WHERE id=?",
                    (event["seq"], discussion.id),
                )

        if step.job.operation == "muse.history":
            writer.event(
                "muse.history_observed",
                job=step.job,
                object_id=discussion.id,
                detail={"session_id": discussion.remote_session, "bounded": True},
            )

    step.commit_checkpoint({"history_saved": True}, write_batch=save)


def _may_be_delivered(exchange):
    return (
        exchange.state not in ("prepared", "failed_before_send")
        and exchange.bytes_handed_off is not False
        and exchange.submitted_at_ms is not None
        and exchange.submitted_observation_high_water is not None
        and exchange.submission_step_id is not None
        and exchange.outbound_payload is not None
        and (exchange.delivery_proof is None or exchange.delivery_proof.kind != "not_submitted")
    )


def _hydrate(store, discussion, exchange, conn):
    count, total = conn.execute(
        "SELECT count(*),COALESCE(sum(length(CAST(raw_event AS BLOB))),0) "
        "FROM muse_observations WHERE discussion_id=?",
        (discussion.id,),
    ).fetchone()
    if count > MAX_OBSERVATIONS or total > MAX_HYDRATION_BYTES:
        _error(
            m.ErrorCode.LIMIT_REACHED,
            "Muse association history exceeds the hydration bound",
            "Retain observations; do not infer completion or discard competitors",
        )
    rows = conn.execute(
        "SELECT rowid,* FROM muse_observations WHERE discussion_id=? ORDER BY rowid",
        (discussion.id,),
    ).fetchall()
    first_seen = {}
    for row in rows:
        key = row["message_id"] or row["identity_key"]
        first_seen.setdefault(key, row["rowid"])
    # Enrichment cannot make an already-observed message younger than this attempt.
    eligible = [
        row
        for row in rows
        if first_seen[row["message_id"] or row["identity_key"]]
        > exchange.submitted_observation_high_water
    ]
    events = [json.loads(row["raw_event"]) for row in eligible]
    memory = ObservationMemory()
    if exchange.observation_memory:
        memory.ambiguous = exchange.observation_memory.ambiguous
        memory.competing = {tuple(key) for key in exchange.observation_memory.competing}
        memory.disqualified = {tuple(key) for key in exchange.observation_memory.disqualified}
    prior = Association("none", None, 0, memory=memory)
    view = _exchange_view(store, discussion, exchange, conn)
    others = [
        store.decode(m.Exchange, row)
        for row in conn.execute(
            "SELECT * FROM exchanges WHERE discussion_id=? AND id<>?",
            (discussion.id, exchange.id),
        )
    ]
    for row, event in zip(eligible, events, strict=True):
        if user_echo(view, [event]) is None:
            continue
        for other in others:
            if (
                _may_be_delivered(other)
                and first_seen[row["message_id"] or row["identity_key"]]
                > other.submitted_observation_high_water
                and user_echo(_exchange_view(store, discussion, other, conn), [event]) is not None
            ):
                memory.ambiguous = True
                memory.competing.add(tuple(json.loads(_event_key(event))))
                break
    association = associate_reply(view, events, prior=prior)
    blocked = view.earlier_unresolved or association.state == "ambiguous"
    if association.state == "ambiguous":
        association.memory.ambiguous = True
    echo = None if blocked else user_echo(view, list(association.memory.events.values()))
    return rows, view, association, echo


def _associate(step, discussion, exchange):
    if not _may_be_delivered(exchange):
        return
    proof = None
    with step.store.connection() as conn:
        rows, view, association, echo = _hydrate(step.store, discussion, exchange, conn)
        if exchange.delivery_proof is None and echo is not None:
            observed = next(
                (
                    row
                    for row in rows
                    if _event_key(json.loads(row["raw_event"])) == _event_key(echo)
                ),
                None,
            )
            if observed is not None:
                proof = m.MuseProof(
                    kind="user_echo",
                    step_id=exchange.submission_step_id,
                    observation_id=observed["id"],
                    outbound_digest=exchange.outbound_digest,
                )
    memory = m.MuseMemory(
        ambiguous=association.memory.ambiguous,
        competing=[list(key) for key in sorted(association.memory.competing)],
        disqualified=[list(key) for key in sorted(association.memory.disqualified)],
        high_water=max((row["rowid"] for row in rows), default=0),
        association="ambiguous" if view.earlier_unresolved else association.state,
    )
    candidates = (
        []
        if view.earlier_unresolved or association.state == "ambiguous"
        else list(association.progress) + ([association.reply] if association.reply else [])
    )
    state = exchange.state
    if proof and state in ("send_uncertain", "submitting"):
        state = "sent"
    if association.reply is not None and state in ("sent", "send_uncertain"):
        state = "reply_observed"

    def save(writer):
        writer.execute(
            (
                "UPDATE exchanges SET state=?,delivery_proof=COALESCE(delivery_proof,?)"
                ",observation_memory=? WHERE id=?"
            ),
            (
                state,
                m.canonical_json(proof.model_dump(mode="json")).decode() if proof else None,
                m.canonical_json(memory.model_dump(mode="json")).decode(),
                exchange.id,
            ),
        )
        for event in ([echo] if echo else []) + candidates:
            writer.execute(
                (
                    "UPDATE messages SET exchange_id=?,association=? WHERE discussion_id=? "
                    "AND identity_key=? AND (exchange_id IS NULL OR exchange_id=?)"
                ),
                (
                    exchange.id,
                    "late"
                    if exchange.state == "closed"
                    else "user_echo"
                    if event is echo
                    else "progress"
                    if event in association.progress
                    else "reply",
                    discussion.id,
                    _event_key(event),
                    exchange.id,
                ),
            )

    step.commit_checkpoint({"association_saved": True}, write_batch=save)
    original = step.store.get(m.JobStep, exchange.submission_step_id)
    if proof is not None and original.state in UNCERTAIN:

        def verify(conn, target):
            current = step.store.get(m.Exchange, exchange.id, conn=conn)
            observed = step.store.get(m.MuseObservation, proof.observation_id, conn=conn)
            if (
                current.delivery_proof != proof
                or observed.discussion_id != discussion.id
                or user_echo(
                    _exchange_view(step.store, discussion, current, conn), [observed.raw_event]
                )
                is None
            ):
                _error(
                    m.ErrorCode.EXTERNAL_STATE_UNKNOWN, "Original delivery proof is not definite"
                )

        step.executor.settle_uncertain(
            original.id,
            expected_target=original.target,
            verify=verify,
            result=m.JobResult(
                coverage_status="bounded",
                stop_reason="original_user_echo",
                data={
                    "exchange_id": exchange.id,
                    "backend": discussion.backend,
                    "mock": discussion.backend == "fixture",
                    "research_complete": False,
                },
            ),
        )
        step.commit_checkpoint(
            {"reconciled": True},
            write_batch=lambda writer: writer.execute(
                "UPDATE discussions SET quarantine_step_id=NULL "
                "WHERE id=? AND quarantine_step_id=?",
                (discussion.id, original.id),
            ),
        )


def _read_job(step):
    discussion = step.store.get(m.Discussion, step.payload["discussion_id"])
    sessions = step.job.operation == "muse.sessions"
    target, payload = read_action(discussion, sessions=sessions)
    if target != step.target or payload != step.payload["read_payload"]:
        _error(m.ErrorCode.REFERENCE_INVALID, "Original read target changed")
    verify = external_action_verifier(
        step,
        permit_id=step.payload["permit_id"],
        use=_use(discussion, "muse.read", action_digest(target, payload)),
        load_payload=lambda conn, target: read_action(
            step.store.get(m.Discussion, discussion.id, conn=conn), sessions=sessions
        )[1],
    )
    token = step.before_external_action(step.target, verify=verify)
    assert token is None
    gateway = _gateway(step, discussion)
    try:
        result = (
            gateway.sessions_list()
            if sessions
            else gateway.chat_history(session_id=discussion.remote_session, limit=200)
        )
    finally:
        if discussion.backend == "live":
            gateway.close()
    if not 200 <= result.status < 300:
        _error(m.ErrorCode.PROTOCOL_MISMATCH, "Muse history response is unusable")
    if sessions:
        _reconcile_open(step, discussion, result.body)
    else:
        events = _normalize(_events(result.body), discussion.remote_session)
        _observe(step, discussion, events)
        with step.store.connection() as conn:
            exchanges = [
                step.store.decode(m.Exchange, row)
                for row in conn.execute(
                    "SELECT * FROM exchanges WHERE discussion_id=? ORDER BY rowid", (discussion.id,)
                )
            ]
        for exchange in exchanges:
            if exchange.submission_step_id is not None and exchange.outbound_payload is not None:
                _associate(step, discussion, exchange)
    return m.JobResult(
        coverage_status="bounded",
        stop_reason="history_page",
        data={
            "discussion_id": discussion.id,
            "backend": discussion.backend,
            "mock": discussion.backend == "fixture",
            "research_complete": False,
        },
    )


def _reconcile_open(step, discussion, body):
    failed = False
    try:
        sessions = json.loads(body)["sessions"]
        if not isinstance(sessions, list) or len(sessions) > 200:
            raise ValueError
        matches = [
            session
            for session in sessions
            if isinstance(session, dict)
            and session.get("request_marker") == discussion.open_payload.marker
            and isinstance(session.get("session_id"), str)
            and 0 < len(session["session_id"]) <= 256
        ]
    except (ValueError, TypeError, KeyError, RecursionError):
        failed = True
    if failed or len(matches) != 1:
        _error(
            m.ErrorCode.EXTERNAL_STATE_UNKNOWN,
            "Session mapping is not proven",
            "Keep open uncertainty; title equality or absence from a list is insufficient",
        )
    session = matches[0]["session_id"]
    original = step.store.get(m.JobStep, discussion.open_step_id)
    if original.state not in UNCERTAIN:
        return
    digest = hashlib.sha256(m.canonical_json(discussion.open_payload.body)).hexdigest()
    proof = m.MuseProof(kind="session_marker", step_id=original.id, outbound_digest=digest)
    event = {
        "session_id": session,
        "event_name": "session.mapping",
        "seq": 0,
        "request_marker": discussion.open_payload.marker,
        "payload": {},
    }
    event = _normalize([event], session)[0]
    _observe(step, discussion, [event])

    def verify(conn, target):
        current = step.store.get(m.Discussion, discussion.id, conn=conn)
        if (
            current.open_payload != discussion.open_payload
            or not conn.execute(
                "SELECT 1 FROM muse_observations WHERE discussion_id=? AND observation_digest=?",
                (discussion.id, m.payload_digest(event)),
            ).fetchone()
        ):
            _error(m.ErrorCode.EXTERNAL_STATE_UNKNOWN, "Original session marker proof is missing")

    def save(writer):
        writer.execute(
            (
                "UPDATE discussions SET remote_session=?,open_delivery_proof=?,open_att"
                "empt_state='sent',state='open' WHERE id=?"
            ),
            (session, m.canonical_json(proof.model_dump(mode="json")).decode(), discussion.id),
        )

    step.executor.settle_uncertain(
        original.id,
        expected_target=original.target,
        verify=verify,
        write_batch=save,
        result=m.JobResult(
            coverage_status="bounded",
            stop_reason="original_session_marker",
            data={
                "discussion_id": discussion.id,
                "backend": discussion.backend,
                "mock": discussion.backend == "fixture",
            },
        ),
    )


def _cursor(payload, key):
    raw = m.canonical_json(payload)
    return base64.urlsafe_b64encode(raw + hmac.digest(key, raw, "sha256")).decode().rstrip("=")


def _uncursor(store, cursor, binding):
    bad = False
    try:
        if len(cursor) > 4096 or re.fullmatch(r"[A-Za-z0-9_-]+", cursor) is None:
            raise ValueError
        raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        body, signature = raw[:-32], raw[-32:]
        key = next(
            (
                key.key_material
                for key in store.workspace_verification_keys(CONTENT_CURSOR_KEY)
                if hmac.compare_digest(signature, hmac.digest(key.key_material, body, "sha256"))
            ),
            None,
        )
        if key is None:
            raise ValueError
        value = json.loads(body)
        if (
            key is None
            or _cursor(value, key) != cursor
            or set(value) != {*binding, "high_water", "after"}
            or any(value[k] != v for k, v in binding.items())
            or any(type(value[k]) is not int or value[k] < 0 for k in ("high_water", "after"))
            or value["after"] > value["high_water"]
        ):
            raise ValueError
    except (ValueError, TypeError, KeyError, StopIteration):
        bad = True
    if bad:
        _error(m.ErrorCode.INPUT_INVALID, "Tampered, foreign or invalid Muse cursor")
    return value


def read_muse_exchange(ctx, request):
    register_operations()
    exchange = ctx.store.get(m.Exchange, request.exchange_id)
    discussion = ctx.store.get(m.Discussion, exchange.discussion_id)
    binding = {"purpose": "muse.read", "discussion_id": discussion.id, "exchange_id": exchange.id}
    cursor = _uncursor(ctx.store, request.cursor, binding) if request.cursor else None
    refresh = None
    if request.refresh:
        refresh = _queue_read(ctx, discussion, request.access_grant_id, exchange_id=exchange.id)
        if refresh.status == "error":
            return refresh
    with ctx.store.connection() as conn:
        high = (
            cursor["high_water"]
            if cursor
            else conn.execute(
                "SELECT COALESCE(MAX(rowid),0) FROM muse_observations WHERE discussion_id=?",
                (discussion.id,),
            ).fetchone()[0]
        )
        after = cursor["after"] if cursor else 0
        rows = conn.execute(
            (
                "SELECT rowid,* FROM muse_observations WHERE discussion_id=? AND rowid>"
                "? AND rowid<=? ORDER BY rowid LIMIT ?"
            ),
            (discussion.id, after, high, READ_PAGE + 1),
        ).fetchall()
        messages = []
        for row in conn.execute(
            "SELECT * FROM messages WHERE exchange_id=? AND id IN "
            "(SELECT message_id FROM muse_observations WHERE discussion_id=? "
            "AND rowid>? AND rowid<=?) ORDER BY seq,id LIMIT ?",
            (exchange.id, discussion.id, after, high, READ_PAGE),
        ):
            message = ctx.store.decode(m.Message, row).model_dump(mode="json")
            latest = conn.execute(
                "SELECT raw_event,observed_at FROM muse_observations "
                "WHERE message_id=? AND rowid<=? ORDER BY rowid DESC LIMIT 1",
                (message["id"], high),
            ).fetchone()
            payload = json.loads(latest["raw_event"]).get("payload", {})
            message["text"] = payload.get("display_text") or payload.get("content") or ""
            message["raw_status"] = (
                str(payload["status"]) if payload.get("status") is not None else None
            )
            message["observed_at"] = latest["observed_at"]
            messages.append(message)

    page = rows[:READ_PAGE]
    data = {
        "discussion_id": discussion.id,
        "exchange_id": exchange.id,
        "exchange": exchange.model_dump(mode="json"),
        "messages": messages,
        "observations": [
            {
                **ctx.store.decode(
                    m.MuseObservation, {key: row[key] for key in row.keys() if key != "rowid"}
                ).model_dump(mode="json"),
                "evidence_ref": {"kind": "muse_observation", "id": row["id"], "revision": 1},
                "mock": row["backend"] == "fixture",
                "research_complete": False,
            }
            for row in page
        ],
        "backend": discussion.backend,
        "mock": discussion.backend == "fixture",
        "research_complete": False,
        "association": exchange.observation_memory.association
        if exchange.observation_memory
        else "none",
    }
    if refresh:
        data.update(
            refresh_job_id=refresh.data["job_id"],
            state=refresh.data["state"],
            worker_available=refresh.data["worker_available"],
        )
    next_cursor = (
        _cursor(
            {**binding, "high_water": high, "after": page[-1]["rowid"]},
            ctx.store.get_workspace_key(CONTENT_CURSOR_KEY).key_material,
        )
        if len(rows) > READ_PAGE
        else None
    )
    return m.Envelope(status="accepted" if refresh else "ok", data=data, next_cursor=next_cursor)


def close_muse_exchange(ctx, request):
    with ctx.store.transaction() as conn:

        def close():
            exchange = ctx.store.get(m.Exchange, request.exchange_id, conn=conn)
            discussion = ctx.store.get(m.Discussion, exchange.discussion_id, conn=conn)
            original = (
                ctx.store.get(m.JobStep, exchange.submission_step_id, conn=conn)
                if exchange.submission_step_id
                else None
            )
            unresolved = original is not None and original.state in ("submitting", *UNCERTAIN)
            if unresolved and request.outcome != "abandoned":
                _error(
                    m.ErrorCode.EXTERNAL_STATE_UNKNOWN,
                    "Delivery remains uncertain",
                    "Only abandonment is allowed until original delivery is reconciled",
                )
            if exchange.outcome is not None and exchange.outcome != request.outcome:
                _error(m.ErrorCode.IDEMPOTENCY_CONFLICT, "Exchange outcome is immutable")
            conn.execute(
                "UPDATE exchanges SET state='closed',outcome=? WHERE id=?",
                (request.outcome, exchange.id),
            )
            if unresolved:
                conn.execute(
                    "UPDATE discussions SET quarantine_step_id=? WHERE id=?",
                    (original.id, discussion.id),
                )
            elif exchange.state == "prepared" and exchange.job_id:
                conn.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (exchange.job_id,))
            ctx.store.event(
                conn,
                "muse.exchange_closed",
                object_id=exchange.id,
                detail={"outcome": request.outcome, "delivery_unresolved": unresolved},
            )
            return {
                "exchange_id": exchange.id,
                "state": "closed",
                "outcome": request.outcome,
                "delivery_unresolved": unresolved,
                "backend": discussion.backend,
                "mock": discussion.backend == "fixture",
                "research_complete": False,
            }

        data = ctx.store.mutate_once(
            conn, "muse.close", request.request_key, request.model_dump(mode="json"), close
        )
    return m.Envelope(status="ok", data=data)


def register_operations(registry=None):
    target = OPERATIONS if registry is None else registry
    for name, handler, side, uncertain in (
        ("muse.open", _mutate, "external_submit", "open_uncertain"),
        ("muse.send", _mutate, "external_submit", "send_uncertain"),
        ("muse.history", _read_job, "safe_read", "needs_reconcile"),
        ("muse.sessions", _read_job, "safe_read", "needs_reconcile"),
    ):
        if name not in target:
            register_operation(name, handler, side, uncertain_state=uncertain, registry=target)
