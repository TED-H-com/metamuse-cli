"""Human-only bounded Muse approval, using existing Permit rows and reservations."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, replace
from datetime import timedelta

from metamuse import models as m
from metamuse import safety
from metamuse.config import load_config


@dataclass(frozen=True)
class _Preflight:
    """Process-owned validated inputs, never a serialized grant or caller flag."""

    store: object
    review: bytes
    policy: bytes
    packet: bytes
    open_jobs: bytes
    creation_request: bytes | None = None


def _current(ctx):
    # Do not approve a cached account/configuration after an operator changes it.
    fresh = copy.copy(ctx)
    fresh.policy = load_config(ctx.paths.root)
    fresh.limits = fresh.policy.limits
    return fresh


def _packet(store, conn, packet_id, project):
    packet = store.get(m.Packet, packet_id, conn=conn)
    if (
        packet.project_id != project
        or packet.stale
        or hashlib.sha256(packet.rendered_text.encode()).hexdigest() != packet.digest
    ):
        safety._grant_error("Research packet is changed, stale or outside this project")
    store.validate_evidence(project, packet.selection, conn=conn, source_only=True)
    # Evidence validation accepts historical versions; research must also reject new revisions.
    for ref in packet.selection:
        model = m.ItemRevision if ref.kind == "item_revision" else m.TranscriptSegment
        latest = store.get(model, ref.id, conn=conn)
        if latest.revision != ref.revision:
            safety._grant_error("Research packet has a newer source revision; prepare a new packet")
    return packet


def _open_jobs(conn, request_key):
    return m.canonical_json(
        [
            dict(row)
            for row in conn.execute(
                "SELECT id,payload FROM jobs WHERE operation='muse.open' AND request_key=?",
                (request_key,),
            )
        ]
    )


def _preflight(ctx, request, *, packet_id, rounds, max_reads, expires_at, request_key, conn):
    from metamuse._muse_lifecycle import prepare_open

    ctx.require_interactive()
    if conn.in_transaction:
        safety._grant_error("Prepare research privacy checks before opening a write transaction")
    ctx = _current(ctx)
    if not ctx.policy.muse.enabled:
        safety._grant_error("Enable Muse before approving research")
    request = m.MuseOpenRequest.model_validate(request, context={"limits": ctx.limits})
    if request.discussion_id is not None or request.access_grant_id is not None:
        safety._grant_error("Research approval needs a new open request without a grant")
    # Reuse the existing key constraints for the human approval receipt too.
    m.ProjectCreateRequest(name="research", request_key=request_key)
    ctx.store.get(m.Project, request.project_id, conn=conn)
    open_jobs = _open_jobs(conn, request.request_key)
    prepared = prepare_open(ctx, request)
    safety._label(prepared.target["provider"])
    safety._label(prepared.target["account"])
    if prepared.target["backend"] != ctx.policy.muse.backend:
        safety._grant_error("Research backend differs from current configuration")
    packet = _packet(ctx.store, conn, packet_id, request.project_id)
    if safety.redact_text(packet.rendered_text)[0] != packet.rendered_text:
        raise m.CoreError(
            m.ErrorCode.REFERENCE_INVALID,
            "Packet is stale; prepare a new packet",
            next_action="Prepare a new packet before reviewing or sending it",
        )
    scope = safety.ResearchScope(
        discussion_id=prepared.target["intent_id"],
        topic=request.topic,
        open_request_key=request.request_key,
        open_digest=safety.action_digest(prepared.target, prepared.payload),
        backend=prepared.target["backend"],
        packet_id=packet.id,
        packet_digest=packet.digest,
        selection_digest=safety.snapshot_digest(packet.selection),
        rounds=rounds,
        max_reads=max_reads,
    )
    if rounds > ctx.limits.max_exchanges:
        safety._grant_error("Research rounds exceed the workspace exchange limit")
    if len(packet.rendered_text.encode()) > ctx.limits.packet_max_utf8_bytes:
        safety._grant_error("Research packet exceeds the workspace byte limit")
    expiry = safety._timestamp(expires_at).isoformat()
    review = {
        "project_id": request.project_id,
        "provider": prepared.target["provider"],
        "account": prepared.target["account"],
        "scope": scope.model_dump(mode="json"),
        "open_request": request.model_dump(mode="json"),
        "open_payload": prepared.model_dump(mode="json"),
        "packet_selection": [ref.model_dump(mode="json") for ref in packet.selection],
        "expires_at": expiry,
        "request_key": request_key,
        "access_max_uses": max_reads + 1,
        "share_max_uses": rounds,
    }
    if _open_jobs(conn, request.request_key) != open_jobs:
        safety._grant_error("Research open request changed during preparation; review again")
    return _Preflight(
        store=ctx.store,
        review=m.canonical_json(review),
        policy=m.canonical_json(ctx.policy),
        packet=m.canonical_json(packet),
        open_jobs=open_jobs,
    )


def prepare_research(ctx, request, *, packet_id, rounds, max_reads, expires_at, request_key, conn):
    preflight = _preflight(
        ctx,
        request,
        packet_id=packet_id,
        rounds=rounds,
        max_reads=max_reads,
        expires_at=expires_at,
        request_key=request_key,
        conn=conn,
    )
    return json.loads(preflight.review)


def _recheck(ctx, conn, preflight):
    """Compare current raw state with validated bytes; no privacy/wire preparation."""
    ctx.require_interactive()
    if type(preflight) is not _Preflight or preflight.store is not ctx.store:
        safety._grant_error("Research creation requires process-owned privacy preparation")
    ctx = _current(ctx)
    if m.canonical_json(ctx.policy) != preflight.policy:
        safety._grant_error("Research configuration changed during preparation; review again")
    review = json.loads(preflight.review)
    ctx.store.get(m.Project, review["project_id"], conn=conn)
    packet = _packet(ctx.store, conn, review["scope"]["packet_id"], review["project_id"])
    if (
        m.canonical_json(packet) != preflight.packet
        or _open_jobs(conn, review["scope"]["open_request_key"]) != preflight.open_jobs
    ):
        safety._grant_error("Research inputs changed during preparation; review again")
    return review


def preview(ctx, review):
    scope = review["scope"]
    packet_command = safety.operator_command(ctx, "packet", "preview", scope["packet_id"])
    # Reuse the existing safe terminal formatter; source-like fields stay quoted.
    return m.ApprovalPreview(
        operation="muse.research",
        permit_operation="muse.send",
        kind="share",
        project_id=review["project_id"],
        provider=review["provider"],
        account=review["account"],
        action_digest=m.payload_digest(review),
        destination=review["open_payload"]["target"]["origin"]
        + " discussion "
        + scope["discussion_id"],
        topic=scope["topic"],
        packet_id=scope["packet_id"],
        packet_digest=scope["packet_digest"],
        packet_preview_command=packet_command,
        summary="\n".join(
            [
                "Research consent: host-authored questions on this topic may vary.",
                "Topic: " + safety.redact_text(scope["topic"])[0],
                "Discussion: " + scope["discussion_id"],
                "Open request key: " + scope["open_request_key"],
                "Backend: " + scope["backend"],
                "Open action digest: " + scope["open_digest"],
                "Packet: " + scope["packet_id"] + "; SHA-256 " + scope["packet_digest"],
                "Selection digest: " + scope["selection_digest"],
                "Inspect exact packet text: " + packet_command,
                f"Budget: one open + {scope['max_reads']} remote reads; "
                f"{scope['rounds']} questions.",
                "Identities and bounds are enforced. "
                "Free-text topic meaning is not machine proved.",
                "Host instruction: stay on this topic; include no other data in questions.",
                "No collect, post, reply, social or external publishing permission.",
                "New packet/revision/account/project/discussion/topic/open key "
                "requires new approval.",
            ]
        ),
        request_file="",
        request={},
        next_action="",
    )


def issue_research(ctx, reviewed):
    ctx.require_interactive()
    inputs = copy.deepcopy(reviewed)
    with ctx.store.connection() as conn:
        preflight = _preflight(
            ctx,
            inputs["open_request"],
            packet_id=inputs["scope"]["packet_id"],
            rounds=inputs["scope"]["rounds"],
            max_reads=inputs["scope"]["max_reads"],
            expires_at=inputs["expires_at"],
            request_key=inputs["request_key"],
            conn=conn,
        )
    if preflight.review != m.canonical_json(reviewed):
        safety._grant_error("Research approval changed during review; review again")
    with ctx.store.transaction() as conn:
        fresh = _recheck(ctx, conn, preflight)
        if preflight.review != m.canonical_json(reviewed):
            safety._grant_error("Research approval changed during review; review again")
        reviewed = fresh
        replayed = (
            conn.execute(
                "SELECT 1 FROM request_receipts "
                "WHERE operation='permit.research' AND request_key=?",
                (reviewed["request_key"],),
            ).fetchone()
            is not None
        )

        def issue():
            now = safety._timestamp(ctx.clock())
            expires = safety._timestamp(reviewed["expires_at"])
            if not now < expires <= now + timedelta(hours=24):
                safety._grant_error("Research expiry must be in the next 24 hours")
            result = {}
            for kind, scope, uses in (
                (
                    "access",
                    {
                        "operations": ["muse.open", "muse.read"],
                        "target_digests": [reviewed["scope"]["open_digest"]],
                    },
                    reviewed["access_max_uses"],
                ),
                (
                    "share",
                    {"packet_digests": [reviewed["scope"]["packet_digest"]]},
                    reviewed["share_max_uses"],
                ),
            ):
                scope["research"] = reviewed["scope"]
                permit = m.Permit(
                    id=m.new_id("pmt"),
                    project_id=reviewed["project_id"],
                    kind=kind,
                    scope=scope,
                    provider=reviewed["provider"],
                    account=reviewed["account"],
                    issuer="local_operator",
                    issued_at=now.isoformat(),
                    expires_at=reviewed["expires_at"],
                    max_uses=uses,
                )
                ctx.store.insert(permit, conn=conn)
                ctx.store.event(
                    conn,
                    "permit.created",
                    object_id=permit.id,
                    detail={"research_approval_digest": m.payload_digest(reviewed)},
                )
                result[kind + "_grant_id"] = permit.id
            return result

        result = ctx.store.mutate_once(
            conn,
            "permit.research",
            reviewed["request_key"],
            reviewed,
            issue,
        )
        data = {
            **result,
            "discussion_id": reviewed["scope"]["discussion_id"],
            "approval_digest": m.payload_digest(reviewed),
            "replayed": replayed,
        }
        for kind in ("access", "share"):
            data[kind] = ctx.store.get(m.Permit, result[kind + "_grant_id"], conn=conn).model_dump(
                mode="json"
            )
    return m.Envelope(status="ok", data=data)


def prepare_creation(ctx, scope, request):
    """Manual creation preflights only new receipts, before owning the writer."""
    creation_request = m.canonical_json(request)
    with ctx.store.connection() as conn:
        if conn.execute(
            "SELECT 1 FROM request_receipts WHERE operation='permit.create' AND request_key=?",
            (request.request_key,),
        ).fetchone():
            return None  # Existing receipt replay never creates or renews authority.
        preflight = _preflight(
            ctx,
            {
                "project_id": request.project_id,
                "topic": scope.topic,
                "request_key": scope.open_request_key,
            },
            packet_id=scope.packet_id,
            rounds=scope.rounds,
            max_reads=scope.max_reads,
            expires_at=request.expires_at,
            request_key=request.request_key,
            conn=conn,
        )
    return replace(preflight, creation_request=creation_request)


def validate_creation(ctx, conn, scope, request, *, preflight):
    """Atomically recheck manual creation against its process-owned preflight."""
    review = _recheck(ctx, conn, preflight)
    if (
        preflight.creation_request != m.canonical_json(request)
        or review["scope"] != scope.model_dump(mode="json")
        or request.provider != review["provider"]
        or request.account != review["account"]
        or request.max_uses > (scope.rounds if request.kind == "share" else scope.max_reads + 1)
        or safety._timestamp(request.expires_at)
        > safety._timestamp(ctx.clock()) + timedelta(hours=24)
    ):
        safety._grant_error()


def verify_research_use(store, conn, permit, scope, use, check_remaining):
    """Reconstruct the actual action from durable domain state, at queue and reservation."""
    from metamuse._muse_lifecycle import _identity, _prepared, _target, read_action

    if use.discussion_id != scope.discussion_id:
        safety._grant_error()
    discussion = store.get(m.Discussion, scope.discussion_id, conn=conn)
    policy = load_config(store.paths.root)
    current = policy.muse.binding
    provider, account = (
        ("fixture", "fixture")
        if policy.muse.backend == "fixture"
        else (current.provider, current.account)
        if current
        else (None, None)
    )
    if (
        not policy.muse.enabled
        or policy.muse.backend != scope.backend
        or (provider, account) != (permit.provider, permit.account)
        or (discussion.provider, discussion.account) != (provider, account)
        or discussion.backend != scope.backend
        or discussion.project_id != use.project_id
        or discussion.topic != scope.topic
        or discussion.open_request_key != scope.open_request_key
        or discussion.id != _identity("dsc", use.project_id, scope.open_request_key)
        or discussion.open_payload is None
        or safety.action_digest(discussion.open_payload.target, discussion.open_payload.payload)
        != scope.open_digest
        or permit.max_uses > (scope.rounds if permit.kind == "share" else scope.max_reads + 1)
        or safety._timestamp(permit.expires_at) - safety._timestamp(permit.issued_at)
        > timedelta(hours=24)
        or scope.rounds > policy.limits.max_exchanges
    ):
        safety._grant_error()
    binding = m.MuseBinding(
        provider=discussion.provider, account=discussion.account, auth_ref=discussion.auth_ref
    )
    expected_open = _prepared(
        "session.start",
        _target(
            discussion.project_id, binding, discussion.backend, "muse.open", intent=discussion.id
        ),
        {"title": discussion.topic, "node_id": discussion.project_id},
        "open-" + discussion.id[4:],
        discussion.project_id,
    )
    if discussion.open_payload != expected_open:
        safety._grant_error()
    packet = _packet(store, conn, scope.packet_id, use.project_id)
    if (
        packet.digest != scope.packet_digest
        or safety.snapshot_digest(packet.selection) != scope.selection_digest
        or len(packet.rendered_text.encode()) > policy.limits.packet_max_utf8_bytes
    ):
        safety._grant_error()
    if use.operation == "muse.open":
        if use.digest != scope.open_digest:
            safety._grant_error()
    elif use.operation == "muse.read":
        # sessions.list is permitted solely for reconciling this original unknown open.
        target, payload = read_action(discussion, sessions=discussion.remote_session is None)
        if safety.action_digest(target, payload) != use.digest:
            safety._grant_error()
        if check_remaining:
            reads = conn.execute(
                "SELECT count(*) FROM events e JOIN job_steps s ON s.id=e.object_id "
                "WHERE e.kind='permit.use_reserved' AND json_extract(e.detail,'$.permit_id')=? "
                "AND json_extract(s.target,'$.operation')='muse.read'",
                (permit.id,),
            ).fetchone()[0]
            if reads >= scope.max_reads:
                safety._grant_error("Research read budget exhausted")
    elif use.operation == "muse.send":
        if use.packet_id != packet.id or not discussion.remote_session:
            safety._grant_error()
        # Find the durable prepared exchange matching this exact action, never claimed IDs/hashes.
        rows = conn.execute(
            "SELECT * FROM exchanges WHERE discussion_id=? AND packet_id=?",
            (discussion.id, packet.id),
        )
        found = False
        for row in rows:
            exchange = store.decode(m.Exchange, row)
            prepared = exchange.outbound_payload
            if (
                prepared is None
                or safety.action_digest(prepared.target, prepared.payload) != use.digest
            ):
                continue
            expected_id = _identity("exc", discussion.id, exchange.request_key)
            expected = _prepared(
                "chat.stream",
                _target(
                    discussion.project_id,
                    binding,
                    discussion.backend,
                    "muse.send",
                    intent=expected_id,
                    session=discussion.remote_session,
                    packet=packet.id,
                ),
                {
                    "session_id": discussion.remote_session,
                    "text": exchange.question + "\n\n" + packet.rendered_text,
                    "node_id": discussion.project_id,
                },
                "send-" + expected_id[4:],
                discussion.project_id,
            )
            expected = expected.model_copy(
                update={
                    "payload": {
                        **expected.payload,
                        "question": exchange.question,
                        "packet_text": packet.rendered_text,
                    }
                }
            )
            if (
                exchange.id != expected_id
                or prepared != expected
                or len(exchange.question.encode()) > policy.limits.question_max_utf8_bytes
            ):
                safety._grant_error()
            found = True
        if not found:
            safety._grant_error()
    else:
        safety._grant_error()
