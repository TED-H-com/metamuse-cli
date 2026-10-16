"""Exact human-approved Facebook Page text posts; never retries an entered send."""

from __future__ import annotations

import ipaddress
import json
import queue
import re
import socket
import threading
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from metamuse import models as m
from metamuse import safety
from metamuse.config import load_config
from metamuse.jobs import (
    OPERATIONS,
    UNCERTAIN,
    StepSpec,
    register_operation,
    submit_job,
    worker_available,
)

if TYPE_CHECKING:
    pass

PROVIDER = "facebook-pages"
IMPLEMENTED_TOOLS = frozenset(
    f"metamuse_publication_{name}" for name in ("preview", "submit", "get", "reconcile")
)


def capability_status(ctx):
    policy = load_config(ctx.paths.root) if ctx is not None else None
    local_ready = {"metamuse_publication_get"}
    if policy is not None:
        try:
            _binding(
                policy,
                account=policy.publishing.account,
                destination=policy.publishing.page_id,
            )
        except m.CoreError:
            pass
        else:
            local_ready.add("metamuse_publication_preview")
    remote_environment = (
        "ready" if policy is not None and policy.publishing.backend == "fixture" else "needs_auth"
    )
    return {
        name: {
            "verification": "fixture_passed",
            "environment": "ready" if name.endswith(("preview", "get")) else remote_environment,
            "permission": "granted_scope" if name in local_ready else "not_configured",
        }
        for name in IMPLEMENTED_TOOLS
    }


def _unknown():
    raise m.CoreError(
        m.ErrorCode.EXTERNAL_STATE_UNKNOWN,
        "Publication delivery needs reconciliation; sending again is refused",
        next_action="Inspect the original publication; reconcile its saved receipt. Never resend.",
    )


def _binding(policy, *, account, destination):
    config = policy.publishing
    if not config.enabled:
        raise m.CoreError(
            m.ErrorCode.UNSUPPORTED, "Publishing is disabled in workspace configuration"
        )
    if not config.account or not config.page_id or not config.api_version:
        raise m.CoreError(
            m.ErrorCode.AUTH_REQUIRED, "Configure a Facebook Page, account label and API version"
        )
    if account != config.account or destination != config.page_id:
        raise m.CoreError(
            m.ErrorCode.SCOPE_DENIED, "Publication target differs from operator configuration"
        )
    if config.backend == "live" and (policy.app.network_mode != "live" or config.auth_ref is None):
        raise m.CoreError(
            m.ErrorCode.AUTH_REQUIRED, "Enable live mode and bind the Page token first"
        )
    return config


def _snapshot(store, request, conn, policy):
    config = _binding(policy, account=request.account, destination=request.destination)
    draft = store.get(m.Draft, request.draft_id, conn=conn)
    if draft.project_id != request.project_id:
        raise m.CoreError(m.ErrorCode.REFERENCE_INVALID, "Draft belongs to another project")
    if draft.stale or draft.current_revision != request.revision:
        raise m.CoreError(m.ErrorCode.DATA_STALE, "Preview the current non-stale draft revision")
    revision = store.get(m.DraftRevision, draft.id, revision=request.revision, conn=conn)
    if request.parent is not None or revision.platform not in ("generic", "facebook"):
        raise m.CoreError(m.ErrorCode.UNSUPPORTED, "Only new Facebook Page posts are supported")
    if revision.assets or revision.variant:
        raise m.CoreError(
            m.ErrorCode.UNSUPPORTED, "This publisher supports text only; remove assets and variants"
        )
    if not revision.text.strip():
        raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Publication text is empty")
    store.validate_evidence(draft.project_id, revision.facts_refs, conn=conn)
    payload = {"message": revision.text}
    target = {
        "project_id": draft.project_id,
        "provider": PROVIDER,
        "account": config.account,
        "operation": "publication.send",
        "destination": config.page_id,
        "parent": None,
        "draft_id": draft.id,
        "revision": revision.revision,
        "draft_digest": revision.digest,
        "attachments": [],
        "backend": config.backend,
        "api_version": config.api_version,
        "auth_ref": config.auth_ref,
    }
    return target, payload, safety.action_digest(target, payload)


def _use(target, digest, operation="publication.send"):
    return safety.PermitUse(
        kind="publication",
        project_id=target["project_id"],
        provider=PROVIDER,
        account=target["account"],
        operation=operation,
        digest=digest,
    )


def _approval(ctx, request, target, payload, digest):
    operation = target["operation"]
    request_file = str(ctx.paths.root / ("approval-" + digest + ".json"))
    command = safety.operator_command(
        ctx,
        "permit",
        "approve",
        "--operation",
        operation,
        "--request-file",
        request_file,
        "--exact-digest",
        digest,
        "--request-key",
        "approve-" + digest[:32],
    )
    return m.ApprovalPreview(
        operation=operation,
        permit_operation=operation,
        kind="publication",
        project_id=target["project_id"],
        provider=PROVIDER,
        account=target["account"],
        action_digest=digest,
        destination="Facebook Page " + target["destination"],
        summary=(
            "New Facebook Page text post\n" + payload["message"]
            if operation == "publication.send"
            else "Read the saved Facebook post receipt only; do not send another post"
        ),
        request_file=request_file,
        request=request.model_dump(mode="json"),
        next_action="Save approval.request as the indicated UTF-8 JSON file, review, then run: "
        + command,
    )


def _preview_send(ctx, request):
    with ctx.store.connection() as conn:
        target, payload, digest = _snapshot(ctx.store, request, conn, load_config(ctx.paths.root))
    # Refuse instead of silently changing the exact outbound copy.
    _, findings = safety.redact_text(payload["message"])
    if findings:
        raise m.CoreError(m.ErrorCode.SCOPE_DENIED, "Remove credential-like text before publishing")
    return _approval(ctx, request, target, payload, digest)


def preview_publication(ctx, request):
    register_operations()
    submit = m.PublicationSubmitRequest(
        **request.model_dump(mode="json"), request_key="publish-preview"
    )
    preview = _preview_send(ctx, submit)
    return m.Envelope(
        status="ok",
        data={
            "text": preview.summary.split("\n", 1)[1],
            "account": preview.account,
            "destination": request.destination,
            "action_digest": preview.action_digest,
            "verification": "fixture_passed",
            "live_verified": False,
            "approval": preview.model_dump(mode="json"),
        },
        warnings=["Experimental Facebook Page text publisher; real-account use is unverified."],
    )


def _existing(store, request, digest, conn):
    # Only a proven zero-boundary failure may free an unresolved target. No refund or
    # resend is possible once the durable attempt exists, including after a crash.
    conn.execute(
        "UPDATE publications SET state='not_submitted' WHERE state='queued' "
        "AND draft_id=? AND account=? AND destination=? "
        "AND NOT EXISTS(SELECT 1 FROM publication_attempts a "
        "WHERE a.publication_id=publications.id) "
        "AND EXISTS(SELECT 1 FROM jobs j WHERE j.id=publications.job_id "
        "AND j.remote_state='not_submitted' AND j.state IN ('failed','awaiting_user','cancelled'))",
        (request.draft_id, request.account, request.destination),
    )
    row = conn.execute(
        "SELECT * FROM publications WHERE draft_id=? AND account=? AND destination=? "
        "AND state NOT IN ('sent_verified','not_submitted')",
        (request.draft_id, request.account, request.destination),
    ).fetchone()
    if row and row["action_digest"] != digest:
        _unknown()
    if row:
        return store.decode(m.Publication, row)
    row = conn.execute("SELECT * FROM publications WHERE action_digest=?", (digest,)).fetchone()
    return store.decode(m.Publication, row) if row else None


def _accepted(ctx, publication):
    job = ctx.store.get(m.Job, publication.job_id)
    return m.Envelope(
        status="accepted" if job.state in ("queued", "running") else "ok",
        data={
            "publication_id": publication.id,
            "state": _display_state(ctx.store, publication),
            "job_id": job.id,
            "worker_available": worker_available(ctx.store),
        },
    )


def submit_publication(ctx, request):
    register_operations()
    # Exact durable replay precedes mutable draft/config/auth checks. It can only
    # return the original local publication, never mint permission or send again.
    with ctx.store.connection() as conn:
        receipt = conn.execute(
            "SELECT payload_digest,response FROM request_receipts "
            "WHERE operation='publication.submit' AND request_key=?",
            (request.request_key,),
        ).fetchone()
        if receipt:
            if receipt["payload_digest"] != m.payload_digest(request):
                raise m.CoreError(
                    m.ErrorCode.IDEMPOTENCY_CONFLICT,
                    "Request key has a different publication payload",
                )
            from metamuse.store import _json_loads

            try:
                publication_id = _json_loads(receipt["response"])["publication_id"]
            except (ValueError, TypeError, KeyError):
                raise m.CoreError(
                    m.ErrorCode.STORAGE, "Stored publication receipt is invalid"
                ) from None
            publication = ctx.store.get(m.Publication, publication_id, conn=conn)
            return _accepted(ctx, publication)
    _preview_send(ctx, request)
    policy = load_config(ctx.paths.root)
    with ctx.store.transaction() as conn:
        target, payload, digest = _snapshot(ctx.store, request, conn, policy)
        receipt = conn.execute(
            "SELECT payload_digest FROM request_receipts "
            "WHERE operation='publication.submit' AND request_key=?",
            (request.request_key,),
        ).fetchone()
        if receipt and receipt["payload_digest"] != m.payload_digest(request):
            raise m.CoreError(
                m.ErrorCode.IDEMPOTENCY_CONFLICT, "Request key has a different publication payload"
            )
        existing = _existing(ctx.store, request, digest, conn)
    if existing and existing.state != "not_submitted":
        if _display_state(ctx.store, existing) in ("submitting", "needs_reconcile"):
            _unknown()
        with ctx.store.transaction() as conn:
            ctx.store.mutate_once(
                conn,
                "publication.submit",
                request.request_key,
                request,
                lambda: {"publication_id": existing.id},
            )
        return _accepted(ctx, existing)
    with ctx.store.transaction() as conn:
        safety.verify_permit(ctx.store, request.publication_grant, _use(target, digest), conn=conn)
    publication_id = "pub_" + digest[:32]
    target = {**target, "intent_id": publication_id}

    def prepare(writer, job, steps):
        # Revalidate the current revision and uniqueness in the same job-creation transaction.
        current_target, current_payload, current_digest = _snapshot(
            ctx.store, request, writer._conn, policy
        )
        if (
            digest != current_digest
            or payload != current_payload
            or target != {**current_target, "intent_id": publication_id}
        ):
            raise m.CoreError(m.ErrorCode.DATA_STALE, "Publication changed before queueing")
        prior = _existing(ctx.store, request, digest, writer._conn)
        if prior and prior.state != "not_submitted":
            raise m.CoreError(m.ErrorCode.IDEMPOTENCY_CONFLICT, "Publication was already queued")
        if prior:
            writer.execute(
                "UPDATE publications SET job_id=?,step_id=?,state='queued' WHERE id=?",
                (job.id, steps[0].id, prior.id),
            )
        else:
            writer.insert(
                m.Publication(
                    id=publication_id,
                    project_id=request.project_id,
                    draft_id=request.draft_id,
                    revision=request.revision,
                    account=request.account,
                    destination=request.destination,
                    action_digest=digest,
                    target=target,
                    payload=payload,
                    job_id=job.id,
                    step_id=steps[0].id,
                    created_at=ctx.clock(),
                )
            )
        ctx.store.mutate_once(
            writer._conn,
            "publication.submit",
            request.request_key,
            request,
            lambda: {"publication_id": publication_id},
        )

    result = submit_job(
        ctx.store,
        "publication.send",
        request.request_key,
        request.model_dump(mode="json"),
        project_id=request.project_id,
        steps=[StepSpec(target=target)],
        prepare_domain=prepare,
    )
    if result.status == "error":
        return result
    return _accepted(ctx, ctx.store.get(m.Publication, publication_id))


def _display_state(store, publication):
    if publication.state == "sent_verified":
        return publication.state
    step = store.get(m.JobStep, publication.step_id)
    job = store.get(m.Job, publication.job_id)
    if publication.state in ("queued", "not_submitted") and job.remote_state == "not_submitted":
        return "not_submitted"
    return "needs_reconcile" if step.state in UNCERTAIN else publication.state


def get_publication(ctx, request):
    publication = ctx.store.get(m.Publication, request.publication_id)
    with ctx.store.connection() as conn:
        attempts = [
            ctx.store.decode(m.PublicationAttempt, row).model_dump(mode="json")
            for row in conn.execute(
                "SELECT * FROM publication_attempts WHERE publication_id=?", (publication.id,)
            )
        ]
    data = publication.model_dump(mode="json")
    data.update(
        state=_display_state(ctx.store, publication),
        attempts=attempts,
        live_verified=publication.state == "sent_verified"
        and publication.target["backend"] == "live",
    )
    return m.Envelope(status="ok", data=data)


def _reconcile_action(ctx, request):
    publication = ctx.store.get(m.Publication, request.publication_id)
    _binding(
        load_config(ctx.paths.root),
        account=publication.account,
        destination=publication.destination,
    )
    _check_config(ctx.store, publication)
    if publication.remote_id is None:
        _unknown()  # A missing receipt cannot prove no post exists; never search by text or resend.
    target = {
        **publication.target,
        "operation": "publication.reconcile",
        "publication_id": publication.id,
        "remote_id": publication.remote_id,
    }
    payload = {"remote_id": publication.remote_id, "fields": "id,from,message,is_published"}
    return publication, target, payload, safety.action_digest(target, payload)


def _preview_reconcile(ctx, request):
    _, target, payload, digest = _reconcile_action(ctx, request)
    return _approval(ctx, request, target, payload, digest)


def validate_approval_creation(ctx, conn, scope, permit_request, policy):
    """Recheck operator review under the grant transaction; no redaction or remote I/O."""
    if scope.operations == ["publication.send"]:
        request = m.PublicationSubmitRequest.model_validate(scope.request)
        target, _, digest = _snapshot(ctx.store, request, conn, policy)
    else:
        request = m.PublicationReconcileRequest.model_validate(scope.request)
        publication = ctx.store.get(m.Publication, request.publication_id, conn=conn)
        _binding(policy, account=publication.account, destination=publication.destination)
        if publication.remote_id is None:
            _unknown()
        target = {
            **publication.target,
            "operation": "publication.reconcile",
            "publication_id": publication.id,
            "remote_id": publication.remote_id,
        }
        payload = {"remote_id": publication.remote_id, "fields": "id,from,message,is_published"}
        digest = safety.action_digest(target, payload)
        if any(
            publication.target[key] != getattr(policy.publishing, key)
            for key in ("backend", "api_version", "auth_ref")
        ):
            raise m.CoreError(m.ErrorCode.SCOPE_DENIED, "Publisher binding changed during review")
    if (
        scope.target_digests != [digest]
        or permit_request.exact_digest != digest
        or permit_request.project_id != target["project_id"]
        or permit_request.provider != target["provider"]
        or permit_request.account != target["account"]
    ):
        raise m.CoreError(
            m.ErrorCode.SCOPE_DENIED, "Publication approval differs from the current action"
        )


def reconcile_publication(ctx, request):
    register_operations()
    with ctx.store.connection() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE operation='publication.reconcile' AND request_key=?",
            (request.request_key,),
        ).fetchone()
        if row:
            job = ctx.store.decode(m.Job, row)
            if job.payload != request.model_dump(mode="json"):
                raise m.CoreError(
                    m.ErrorCode.IDEMPOTENCY_CONFLICT,
                    "Request key has a different reconciliation payload",
                )
            original = conn.execute(
                "SELECT * FROM job_steps WHERE job_id=? ORDER BY sequence", (job.id,)
            ).fetchone()
            saved_step = ctx.store.decode(m.JobStep, original)
            return submit_job(
                ctx.store,
                "publication.reconcile",
                request.request_key,
                job.payload,
                project_id=job.project_id,
                steps=[StepSpec(target=saved_step.target)],
            )
    publication, target, _, digest = _reconcile_action(ctx, request)
    if (
        publication.state == "sent_verified"
        and ctx.store.get(m.JobStep, publication.step_id).state == "succeeded"
    ):
        return get_publication(ctx, request)
    with ctx.store.transaction() as conn:
        safety.verify_permit(
            ctx.store,
            request.publication_grant,
            _use(target, digest, "publication.reconcile"),
            conn=conn,
        )
    return submit_job(
        ctx.store,
        "publication.reconcile",
        request.request_key,
        request.model_dump(mode="json"),
        project_id=publication.project_id,
        steps=[StepSpec(target=target)],
    )


def _check_config(store, publication):
    config = _binding(
        load_config(store.paths.root),
        account=publication.account,
        destination=publication.destination,
    )
    if any(
        publication.target[key] != getattr(config, key)
        for key in ("backend", "api_version", "auth_ref")
    ):
        raise m.CoreError(
            m.ErrorCode.SCOPE_DENIED, "Publisher configuration changed; review the original binding"
        )
    return config


def _gateway(step, publication):
    config = _check_config(step.store, publication)
    gateway = step.executor.publishing_gateway
    if config.backend == "fixture":
        if getattr(gateway, "backend", None) != "fixture":
            raise m.CoreError(
                m.ErrorCode.UNSUPPORTED, "Fixture publishing requires an injected test gateway"
            )
        return gateway
    if gateway is not None:
        raise m.CoreError(
            m.ErrorCode.SCOPE_DENIED, "Live publishing uses the fixed Graph API provider"
        )
    token = safety.resolve_secret(
        config.auth_ref,
        provider=PROVIDER,
        account=config.account,
        secret_store=step.executor.secret_store,
    )

    def guard():
        step.guard()
        step.executor.require_network(operation=step.job.operation, target=step.target)
        _check_config(step.store, publication)
        current = safety.resolve_secret(
            config.auth_ref,
            provider=PROVIDER,
            account=config.account,
            secret_store=step.executor.secret_store,
        )
        if current != token:
            raise m.CoreError(m.ErrorCode.AUTH_REQUIRED, "Page credential changed before request")

    return GraphPageGateway(
        token, config.api_version, step.executor.limits.read_timeout_seconds, before_request=guard
    )


def _identity(gateway, publication):
    if gateway.identity() != publication.destination:
        raise m.CoreError(m.ErrorCode.SCOPE_DENIED, "Page token belongs to another identity")


def _verified(publication, observed):
    if (
        not isinstance(observed, dict)
        or observed.get("id") != publication.remote_id
        or observed.get("message") != publication.payload["message"]
        or not isinstance(observed.get("from"), dict)
        or observed["from"].get("id") != publication.destination
        or observed.get("is_published") is not True
    ):
        _unknown()


def _result(publication):
    return m.JobResult(
        coverage_status="scope_satisfied",
        stop_reason="post_read_back",
        observed_scope={
            "publication_id": publication.id,
            "remote_id": publication.remote_id,
            "backend": publication.target["backend"],
            "state": "sent_verified",
        },
    )


def _send(step):
    request = m.PublicationSubmitRequest.model_validate(step.payload)
    publication = step.store.get(m.Publication, step.target["intent_id"])
    if publication.step_id != step.step.id or publication.job_id != step.job.id:
        raise m.CoreError(
            m.ErrorCode.REFERENCE_INVALID, "Publication belongs to another queued step"
        )
    gateway = _gateway(step, publication)
    with step.store.transaction() as conn:
        safety.verify_permit(
            step.store,
            request.publication_grant,
            _use(publication.target, publication.action_digest),
            conn=conn,
        )
    step.guard()
    _identity(gateway, publication)  # Safe read, before committing submission; no SQLite held.
    policy = load_config(step.store.paths.root)

    def load(conn, target):
        current_target, payload, digest = _snapshot(step.store, request, conn, policy)
        if (
            publication.target != {**current_target, "intent_id": publication.id}
            or digest != publication.action_digest
        ):
            raise m.CoreError(
                m.ErrorCode.DATA_STALE, "Publication draft or target changed before sending"
            )
        return payload

    base_verify = safety.external_action_verifier(
        step,
        permit_id=request.publication_grant,
        use=_use(publication.target, publication.action_digest),
        load_payload=load,
    )

    def verify(conn, target):
        digest = base_verify(conn, target)
        if conn.execute(
            "SELECT 1 FROM publication_attempts WHERE publication_id=?", (publication.id,)
        ).fetchone():
            _unknown()
        step.store.insert(
            m.PublicationAttempt(
                id=m.new_id("attempt"),
                publication_id=publication.id,
                step_id=step.step.id,
                action_digest=digest,
                started_at=step.store.clock(),
            ),
            conn=conn,
        )
        conn.execute("UPDATE publications SET state='submitting' WHERE id=?", (publication.id,))
        return digest

    token = step.before_external_action(publication.target, verify=verify)
    token.consume(action_digest=publication.action_digest)
    remote_id = gateway.send(publication.destination, publication.payload["message"])
    if not isinstance(remote_id, str) or not re.fullmatch(
        re.escape(publication.destination) + r"_[0-9]{1,64}", remote_id
    ):
        _unknown()

    # Save the receipt immediately, before read-back. A crash can only lead to a safe read.
    def receipt(writer):
        writer.execute(
            "UPDATE publications SET remote_id=? WHERE id=?", (remote_id, publication.id)
        )
        writer.execute(
            "UPDATE publication_attempts SET remote_id=? WHERE publication_id=?",
            (remote_id, publication.id),
        )

    step.commit_checkpoint({"receipt_saved": True}, write_batch=receipt)
    publication = step.store.get(m.Publication, publication.id)
    observed = gateway.read(remote_id)
    _verified(publication, observed)

    step.commit_checkpoint({"receipt_saved": True, "read_back_verified": True})
    return _result(publication)


def _reconcile(step):
    from metamuse.app import AppContext

    request = m.PublicationReconcileRequest.model_validate(step.payload)
    ctx = AppContext(paths=step.store.paths, store=step.store)
    publication, target, payload, digest = _reconcile_action(ctx, request)
    gateway = _gateway(step, publication)

    def load(conn, actual_target):
        saved = step.store.get(m.Publication, publication.id, conn=conn)
        if saved.remote_id != publication.remote_id or saved.target != publication.target:
            _unknown()
        return payload

    step.before_external_action(
        target,
        verify=safety.external_action_verifier(
            step,
            permit_id=request.publication_grant,
            use=_use(target, digest, "publication.reconcile"),
            load_payload=load,
        ),
    )
    _identity(gateway, publication)
    observed = gateway.read(publication.remote_id)
    _verified(publication, observed)
    step.guard()
    original = step.store.get(m.JobStep, publication.step_id)

    def validate(conn, saved_target):
        saved = step.store.get(m.Publication, publication.id, conn=conn)
        if saved.target != saved_target or saved.remote_id != publication.remote_id:
            _unknown()

    def complete(writer):
        writer.execute(
            "UPDATE job_steps SET checkpoint=json_set(checkpoint, "
            "'$.read_back_verified',json('true')) WHERE id=?",
            (publication.step_id,),
        )

    if original.state in UNCERTAIN:
        step.executor.settle_uncertain(
            publication.step_id,
            expected_target=publication.target,
            verify=validate,
            result=_result(publication),
            write_batch=complete,
        )
    elif original.state == "succeeded" and publication.state == "sent_verified":
        pass
    else:
        _unknown()
    return _result(publication)


def _finish_publication(writer, step, result):
    publication = writer.get(m.Publication, step.target["intent_id"])
    current = writer.get(m.JobStep, step.id)
    if (
        publication.step_id != step.id
        or publication.job_id != step.job_id
        or publication.remote_id is None
        or current.checkpoint.get("read_back_verified") is not True
    ):
        _unknown()
    writer.execute(
        "UPDATE publications SET state='sent_verified',verified_at=metamuse_now() WHERE id=?",
        (publication.id,),
    )
    writer.execute("UPDATE jobs SET remote_state='confirmed' WHERE id=?", (step.job_id,))


def register_operations(registry=None):
    target = OPERATIONS if registry is None else registry
    for name, handler, model, effect in (
        ("publication.send", _send, m.PublicationSubmitRequest, "external_submit"),
        ("publication.reconcile", _reconcile, m.PublicationReconcileRequest, "safe_read"),
    ):
        if name not in target:
            register_operation(
                name,
                handler,
                effect,
                request_model=model,
                registry=target,
                on_finish=_finish_publication if name == "publication.send" else None,
            )
    safety.register_approval_preview(
        "publication.send", _preview_send, request_model=m.PublicationSubmitRequest
    )
    safety.register_approval_preview(
        "publication.reconcile", _preview_reconcile, request_model=m.PublicationReconcileRequest
    )


def _resolve(host, timeout=10):
    """Bound the caller's DNS wait; the daemon holds no token, database or workspace."""
    result = queue.Queue(maxsize=1)

    def resolve():
        try:
            addresses = list(
                {entry[4][0] for entry in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
            )
        except OSError:
            addresses = []
        result.put(addresses)

    threading.Thread(target=resolve, daemon=True).start()
    try:
        return result.get(timeout=max(0.1, timeout))
    except queue.Empty:
        raise m.CoreError(m.ErrorCode.NETWORK_TIMEOUT, "Graph DNS lookup timed out") from None


class GraphPageGateway:
    """Fixed HTTPS Graph endpoints. API version is an explicit operator choice."""

    backend = "live"

    def __init__(
        self,
        token: bytes,
        api_version: str,
        timeout: int,
        *,
        http=None,
        resolver=None,
        before_request=lambda: None,
    ):
        from metamuse._muse_http import CurlHttp

        # This backend accepts a raw Page access token, never a credential-bearing URL.
        if not token or len(token) > 8192 or re.fullmatch(rb"[A-Za-z0-9_|.\-]+", token) is None:
            raise m.CoreError(
                m.ErrorCode.AUTH_REQUIRED, "Import a raw Page access token without whitespace"
            )
        if re.fullmatch(r"v[0-9]{1,2}\.0", api_version) is None:
            raise m.CoreError(
                m.ErrorCode.INPUT_INVALID, "An explicit Graph API version is required"
            )
        self._token = token.decode("ascii")
        self.version, self.timeout = api_version, timeout
        self.http = http or CurlHttp()
        self.resolver = resolver or (lambda host: _resolve(host, min(timeout, 10)))
        self.before_request = before_request

    def _request(self, method, path, *, params=None, message=None):
        self.before_request()
        url = "https://graph.facebook.com/" + self.version + "/" + path
        if params:
            url += "?" + urlencode(params)
        checked = safety.check_url(
            url, purpose="request", resolver=self.resolver, allowed_hosts=("graph.facebook.com",)
        )
        body = None if message is None else urlencode({"message": message}).encode("utf-8")
        error = None
        response = None
        try:
            response = self.http.request(
                method,
                checked.url,
                headers={
                    "Authorization": "Bearer " + self._token,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                body=body,
                checked=checked,
                timeout=self.timeout,
            )
        except Exception:
            error = m.CoreError(
                m.ErrorCode.NETWORK_TIMEOUT, "Facebook Graph request did not finish"
            )
        if error:
            raise error
        # No response headers, raw errors or access tokens enter application errors.
        try:
            peer = str(ipaddress.ip_address(response.peer))
        except ValueError:
            peer = ""
        if peer not in checked.addresses:
            raise m.CoreError(
                m.ErrorCode.SCOPE_DENIED, "Graph peer differs from checked DNS addresses"
            )
        if response.status != 200:
            code = (
                m.ErrorCode.AUTH_REQUIRED
                if response.status in (401, 403)
                else m.ErrorCode.RESOURCE_UNAVAILABLE
            )
            raise m.CoreError(code, "Facebook Graph request was refused")
        if len(response.body) > 65536:
            raise m.CoreError(m.ErrorCode.LIMIT_REACHED, "Graph response exceeds the size cap")
        failure = None

        def reject_constant(_):
            raise ValueError()

        def unique(pairs):
            result = dict(pairs)
            if len(result) != len(pairs):
                raise ValueError()
            return result

        try:
            data = json.loads(
                response.body, parse_constant=reject_constant, object_pairs_hook=unique
            )
            if not isinstance(data, dict) or "error" in data:
                raise ValueError()
        except (ValueError, UnicodeError):
            failure = m.CoreError(
                m.ErrorCode.PROTOCOL_MISMATCH, "Graph returned an invalid response"
            )
        if failure:
            raise failure
        return data

    def identity(self):
        return self._request("GET", "me", params={"fields": "id"}).get("id")

    def send(self, page_id, message):
        if re.fullmatch(r"[0-9]{1,32}", page_id) is None:
            raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Invalid Page identity")
        return self._request("POST", page_id + "/feed", message=message).get("id")

    def read(self, remote_id):
        if re.fullmatch(r"[0-9]{1,32}_[0-9]{1,64}", remote_id) is None:
            raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Invalid stored post receipt")
        return self._request("GET", remote_id, params={"fields": "id,from,message,is_published"})
