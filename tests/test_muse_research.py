"""Bounded research: synthetic FIXTURE/INTEGRATION; no LIVE or credentials."""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import anyio
import pytest
from test_journey import Entrypoints, verify_bundle_or_markdown
from test_muse_lifecycle import grant, packet, run
from test_safety_permits import access_action, issue, load_payload, run_external

from metamuse import _muse_research as research
from metamuse import app, cli, safety
from metamuse import models as m
from metamuse._muse_fixture import OfflineMuseGateway
from metamuse._muse_lifecycle import _use, prepare_open, read_action
from metamuse.config import load_config


@pytest.fixture
def research_ctx(ctx, project):
    path = ctx.paths.root / "metamuse.toml"
    path.write_text(
        path.read_text().replace(
            "[muse]\nenabled = false", '[muse]\nenabled = true\nbackend = "fixture"'
        )
    )
    ctx.policy = load_config(ctx.paths.root)
    ctx.muse_gateway = OfflineMuseGateway(ctx.store)
    return ctx, project, packet(ctx, project)


def review(fixture, *, rounds=2, reads=3, key="research"):
    ctx, project, pkt = fixture
    with ctx.store.connection() as conn:
        return research.prepare_research(
            ctx,
            {"project_id": project, "topic": "Synthetic evidence", "request_key": "open"},
            packet_id=pkt.id,
            rounds=rounds,
            max_reads=reads,
            expires_at=(datetime.fromisoformat(ctx.clock()) + timedelta(hours=1)).isoformat(),
            request_key=key,
            conn=conn,
        )


def approved(fixture, **kwargs):
    ctx, _, _ = fixture
    reviewed = review(fixture, **kwargs)
    grants = research.issue_research(ctx, reviewed).data
    return reviewed, grants


def opened(fixture, **kwargs):
    ctx, _, _ = fixture
    reviewed, grants = approved(fixture, **kwargs)
    result = app.open_muse_discussion(
        ctx,
        {
            **reviewed["open_request"],
            "access_grant_id": grants["access_grant_id"],
        },
    )
    assert result.status == "accepted", result
    run(ctx)
    return reviewed, grants


def send(fixture, grants, *, key="send", **changes):
    ctx, _, pkt = fixture
    return app.submit_muse_exchange(
        ctx,
        {
            "discussion_id": grants["discussion_id"],
            "packet_id": pkt.id,
            "question": "Which facts are supported?",
            "share_grant_id": grants["share_grant_id"],
            "request_key": key,
            **changes,
        },
    )


def test_one_cli_approval_two_mcp_questions_restart_citations_export(tmp_path):
    entry = Entrypoints(tmp_path)
    entry.configure()
    source = entry.inputs / "source.txt"
    source.write_text("Synthetic activity had twelve seats; attendance is unknown.")
    project = entry.cli("project", "create", "--name", "Research", "--request-key", "p")["data"][
        "id"
    ]
    entry.cli(
        "add",
        "--project",
        project,
        "--file",
        source,
        "--kind",
        "text",
        "--request-key",
        "import",
        "--run",
    )
    refs = [
        row["ref"]
        for row in entry.cli("search", "--project", project, "--query", "Synthetic")["data"][
            "results"
        ]
    ]
    selection = entry.inputs / "refs.json"
    selection.write_text(json.dumps(refs))
    pkt = entry.cli(
        "packet",
        "prepare",
        "--project",
        project,
        "--selection-file",
        selection,
        "--request-key",
        "packet",
    )["data"]
    assert entry.cli("packet", "preview", pkt["id"])["data"]["digest"] == pkt["digest"]
    request = {
        "project_id": project,
        "topic": "Synthetic activity\nAccount: fake\u001b[2J\u202e",
        "request_key": "open",
    }
    request_file = entry.inputs / "open.json"
    request_file.write_text(json.dumps(request))
    args = [
        "permit",
        "research",
        "--open-request-file",
        request_file,
        "--packet",
        pkt["id"],
        "--rounds",
        "2",
        "--max-reads",
        "3",
        "--expires-at",
        (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "--request-key",
        "approval",
    ]
    entry.cli(*args, operator=False, code=3)
    entry.cli(*args, operator=True, code=3)
    digest = re.search(r"Action digest: ([a-f0-9]{64})", entry.diagnostics[-1])[1]
    assert "\\u001b[2J\\u202e" in entry.diagnostics[-1]
    assert "\n  | Account: fake" in entry.diagnostics[-1]
    grants = entry.cli(*args, "--exact-digest", digest, operator=True)["data"]
    assert (
        len(entry.cli("permit", "list", "--project", project, operator=True)["data"]["permits"])
        == 2
    )
    assert entry.cli(*args, "--exact-digest", digest, operator=True)["data"]["replayed"]

    async def scenario():
        observations = []
        observed_records = []
        exchanges = []
        async with entry.server() as session:
            await entry.wait_worker_ready(session)
            opened = (
                await entry.call(
                    session,
                    "muse_open",
                    {
                        **request,
                        "access_grant_id": grants["access_grant_id"],
                    },
                )
            )["data"]
            await entry.terminal(session, opened["job_id"])
            for number, question in enumerate(
                ("What is supported?", "Which attendance gaps remain?")
            ):
                sent = (
                    await entry.call(
                        session,
                        "muse_send",
                        {
                            "discussion_id": grants["discussion_id"],
                            "packet_id": pkt["id"],
                            "question": question,
                            "share_grant_id": grants["share_grant_id"],
                            "request_key": f"send-{number}",
                        },
                    )
                )["data"]
                await entry.terminal(session, sent["job_id"])
                exchanges.append(sent["exchange_id"])
                refreshed = (
                    await entry.call(
                        session,
                        "muse_read",
                        {
                            "exchange_id": exchanges[-1],
                            "refresh": True,
                            "access_grant_id": grants["access_grant_id"],
                        },
                    )
                )["data"]
                await entry.terminal(session, refreshed["refresh_job_id"])
                readback = (await entry.call(session, "muse_read", {"exchange_id": exchanges[-1]}))[
                    "data"
                ]
                observed = next(
                    o
                    for o in readback["observations"]
                    if o["raw_event"].get("payload", {}).get("role") == "assistant"
                    and o["raw_event"]["message_id"].startswith(
                        readback["exchange"]["request_marker"]
                    )
                )
                observations.append(observed["evidence_ref"])
                observed_records.append(observed)
                await entry.call(
                    session,
                    "muse_close_exchange",
                    {
                        "exchange_id": exchanges[-1],
                        "outcome": "needs_more_data",
                        "request_key": f"close-{number}",
                    },
                )
        async with entry.server() as session:
            await entry.wait_worker_ready(session)
            refreshed = (
                await entry.call(
                    session,
                    "muse_read",
                    {
                        "exchange_id": exchanges[-1],
                        "refresh": True,
                        "access_grant_id": grants["access_grant_id"],
                    },
                )
            )["data"]
            await entry.terminal(session, refreshed["refresh_job_id"])
            saved = (
                await entry.call(
                    session,
                    "draft_save",
                    {
                        "project_id": project,
                        "text": "Host review: twelve seats; attendance unknown.",
                        "platform": "generic",
                        "facts_refs": refs + observations,
                        "request_key": "draft",
                    },
                )
            )["data"]
            await entry.call(
                session, "draft_preview", {"draft_id": saved["draft_id"], "revision": 1}
            )
            for format_name in ("bundle", "markdown"):
                exported = (
                    await entry.call(
                        session,
                        "export",
                        {
                            "project_id": project,
                            "selection": [
                                {"kind": "draft_revision", "id": saved["draft_id"], "revision": 1}
                            ],
                            "format": format_name,
                            "request_key": "export-" + format_name,
                        },
                    )
                )["data"]
                assert exported["manifest"]["mock"] is True
                assert len(exported["manifest"]["muse_evidence"]) == 2
                verify_bundle_or_markdown(
                    entry.workspace,
                    exported,
                    observed_records,
                    "Host review: twelve seats; attendance unknown.",
                )

    anyio.run(scenario)
    permits = entry.cli("permit", "list", "--project", project, operator=True)["data"]["permits"]
    assert {p["kind"]: p["used"] for p in permits} == {"access": 4, "share": 2}
    entry.hygiene()


@pytest.mark.parametrize(
    "change", ["topic", "openkey", "project", "account", "discussion", "packet"]
)
def test_identities_cannot_expand_scope(research_ctx, change):
    ctx, project, _ = research_ctx
    reviewed, grants = (
        approved(research_ctx)
        if change in {"topic", "openkey", "project"}
        else opened(research_ctx)
    )
    if change in {"topic", "openkey", "project"}:
        request = {**reviewed["open_request"], "access_grant_id": grants["access_grant_id"]}
        request[{"topic": "topic", "openkey": "request_key", "project": "project_id"}[change]] = (
            app.create_project(ctx, {"name": "Other", "request_key": "other"}).data["id"]
            if change == "project"
            else "changed"
        )
        result = app.open_muse_discussion(ctx, request)
    elif change == "account":
        path = ctx.paths.root / "metamuse.toml"
        path.write_text(path.read_text().replace('backend = "fixture"', 'backend = "live"'))
        result = send(research_ctx, grants)
    elif change == "discussion":
        request = m.MuseOpenRequest(
            project_id=project, topic="Another topic", request_key="other-open"
        )
        prepared = prepare_open(ctx, request)
        request.access_grant_id = grant(
            ctx, project, safety.action_digest(prepared.target, prepared.payload)
        )
        other_open = app.open_muse_discussion(ctx, request)
        assert other_open.status == "accepted"
        run(ctx)
        result = send(research_ctx, grants, discussion_id=other_open.data["discussion_id"])
    else:
        result = send(research_ctx, grants, packet_id=packet(ctx, project).id)
    assert result.status == "error", result
    assert ctx.store.get(m.Permit, grants["share_grant_id"]).used == 0


@pytest.mark.parametrize("change", ["revoke", "expiry", "revision", "binding"])
def test_queued_actions_recheck_before_reserving(research_ctx, change):
    ctx, _, pkt = research_ctx
    _, grants = opened(research_ctx)
    queued = send(research_ctx, grants)
    assert queued.status == "accepted", queued
    if change == "revoke":
        safety.revoke_permit(ctx, grants["share_grant_id"], request_key="revoke")
    elif change == "expiry":
        later = (datetime.fromisoformat(ctx.clock()) + timedelta(hours=2)).isoformat()
        ctx.clock = ctx.store.clock = lambda: later
    elif change == "revision":
        old = ctx.store.get(m.ItemRevision, pkt.selection[0].id)
        ctx.store.insert(old.model_copy(update={"revision": 2, "text": "New evidence"}))
    else:
        path = ctx.paths.root / "metamuse.toml"
        path.write_text(path.read_text().replace('backend = "fixture"', 'backend = "live"'))
    run(ctx)
    job = ctx.store.get(m.Job, queued.data["job_id"])
    assert job.state in {"failed", "awaiting_user"}
    assert ctx.store.get(m.Permit, grants["share_grant_id"]).used == 0


def test_pair_idempotency_mismatch_and_partial_rollback(research_ctx, monkeypatch):
    ctx, _, _ = research_ctx
    reviewed = review(research_ctx)
    original = ctx.store.insert
    count = 0

    def fail_second(record, **kwargs):
        nonlocal count
        if isinstance(record, m.Permit):
            count += 1
            if count == 2:
                raise m.CoreError(m.ErrorCode.STORAGE, "Synthetic second insert failure")
        return original(record, **kwargs)

    monkeypatch.setattr(ctx.store, "insert", fail_second)
    with pytest.raises(m.CoreError):
        research.issue_research(ctx, reviewed)
    with ctx.store.connection() as conn:
        assert conn.execute("SELECT count(*) FROM permits").fetchone()[0] == 0
        assert (
            conn.execute("SELECT count(*) FROM events WHERE kind='permit.created'").fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM request_receipts WHERE operation='permit.research'"
            ).fetchone()[0]
            == 0
        )
    monkeypatch.setattr(ctx.store, "insert", original)
    pair = research.issue_research(ctx, reviewed).data
    safety.revoke_permit(ctx, pair["share_grant_id"], request_key="revoke")
    replay = research.issue_research(ctx, reviewed).data
    assert replay["replayed"] and replay["share"]["revoked"] and replay["share"]["used"] == 0
    changed = review(research_ctx, rounds=1)
    with pytest.raises(m.CoreError) as error:
        research.issue_research(ctx, changed)
    assert error.value.detail.code == m.ErrorCode.IDEMPOTENCY_CONFLICT


def test_review_to_creation_binding_race(research_ctx):
    ctx, _, _ = research_ctx
    reviewed = review(research_ctx)
    path = ctx.paths.root / "metamuse.toml"
    path.write_text(path.read_text().replace("enabled = true", "enabled = false"))
    with pytest.raises(m.CoreError):
        research.issue_research(ctx, reviewed)
    with ctx.store.connection() as conn:
        assert conn.execute("SELECT count(*) FROM permits").fetchone()[0] == 0


def test_read_budget_concurrent_last_use_and_round_counter(research_ctx):
    ctx, _, _ = research_ctx
    _, grants = opened(research_ctx, rounds=1, reads=1)
    queued = send(research_ctx, grants)
    assert queued.status == "accepted"
    run(ctx)
    exchange = queued.data["exchange_id"]
    app.close_muse_exchange(
        ctx, {"exchange_id": exchange, "outcome": "needs_more_data", "request_key": "close"}
    )
    assert (
        send(research_ctx, grants, key="another", question="A different question").error.code
        == m.ErrorCode.SCOPE_DENIED
    )
    discussion = ctx.store.get(m.Discussion, grants["discussion_id"])
    target, payload = read_action(discussion)
    use = _use(discussion, "muse.read", safety.action_digest(target, payload))

    def reserve():
        try:
            with ctx.store.transaction() as conn:
                safety.verify_permit(
                    ctx.store, grants["access_grant_id"], use, conn=conn, reserve=True
                )
            return True
        except m.CoreError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: reserve(), range(2))) == [False, True]
    assert ctx.store.get(m.Permit, grants["access_grant_id"]).used == 2


def test_unknown_send_reconciles_without_new_send_or_refund(research_ctx):
    ctx, _, _ = research_ctx
    _, grants = opened(research_ctx)
    ctx.muse_gateway.mode = "send_timeout"
    sent = send(research_ctx, grants)
    run(ctx)
    original_exchange = ctx.store.get(m.Exchange, sent.data["exchange_id"])
    assert ctx.store.get(m.JobStep, original_exchange.submission_step_id).state == "send_uncertain"
    assert ctx.store.get(m.Permit, grants["share_grant_id"]).used == 1
    assert (
        send(research_ctx, grants, key="no-replay").error.code == m.ErrorCode.EXTERNAL_STATE_UNKNOWN
    )
    exchange = sent.data["exchange_id"]
    refreshed = app.read_muse_exchange(
        ctx,
        {"exchange_id": exchange, "refresh": True, "access_grant_id": grants["access_grant_id"]},
    )
    assert refreshed.status == "accepted"
    run(ctx)
    assert ctx.store.get(m.Permit, grants["share_grant_id"]).used == 1
    assert ctx.store.get(m.JobStep, original_exchange.submission_step_id).state == "succeeded"
    assert [route for route, _ in ctx.muse_gateway.calls].count("chat.stream") == 1
    with ctx.store.connection() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM events WHERE kind='permit.use_reserved' "
                "AND json_extract(detail,'$.permit_id')=?",
                (grants["share_grant_id"],),
            ).fetchone()[0]
            == 1
        )


def test_legacy_reservation_fingerprint_with_new_trusted_context(ctx, project):
    target, payload, use = access_action(project)
    target["operation"] = "muse.read"
    use = use.model_copy(
        update={"operation": "muse.read", "digest": safety.action_digest(target, payload)}
    )
    permit = issue(
        ctx,
        project,
        kind="access",
        max_uses=1,
        scope={"operations": ["muse.read"], "target_digests": [use.digest]},
    )
    legacy = use.model_dump(mode="json", exclude={"discussion_id"})
    expected = m.payload_digest({"permit_id": permit["id"], "use": legacy})
    assert legacy["packet_id"] is None

    def handler(step):
        first = safety.external_action_verifier(
            step, permit_id=permit["id"], use=use, load_payload=load_payload(step)
        )
        step.before_external_action(step.target, verify=first)
        with ctx.store.connection() as conn:
            detail = json.loads(
                conn.execute(
                    "SELECT detail FROM events WHERE kind='permit.use_reserved' AND object_id=?",
                    (step.step.id,),
                ).fetchone()[0]
            )
        assert detail["fingerprint"] == expected
        later = safety.external_action_verifier(
            step,
            permit_id=permit["id"],
            use=use.model_copy(update={"discussion_id": "dsc_new_context"}),
            load_payload=load_payload(step),
        )
        step.before_external_action(step.target, verify=later)
        return m.JobResult(coverage_status="bounded")

    assert (
        run_external(ctx, project, handler, target, payload, side_effect="safe_read").state
        == "succeeded"
    )
    assert ctx.store.get(m.Permit, permit["id"]).used == 1


@pytest.mark.parametrize("field,value", [("rounds", 0), ("rounds", 4), ("reads", 201)])
def test_research_rejects_unbounded_or_workspace_excess(research_ctx, field, value):
    with pytest.raises((m.CoreError, ValueError)):
        approved(research_ctx, **{field: value})


def test_expiry_and_mixed_scopes_are_not_minted(research_ctx):
    ctx, _, _ = research_ctx
    reviewed = review(research_ctx)
    for hours in (-1, 25):
        changed = dict(reviewed)
        changed["expires_at"] = (
            datetime.fromisoformat(ctx.clock()) + timedelta(hours=hours)
        ).isoformat()
        with pytest.raises(m.CoreError):
            research.issue_research(ctx, changed)
    scope = reviewed["scope"]
    for kind, mixed in (
        (
            "access",
            {
                "operations": ["collect.remote"],
                "target_digests": [scope["open_digest"]],
                "research": scope,
            },
        ),
        (
            "share",
            {
                "research": scope,
                "snapshot": {
                    "digest": scope["selection_digest"],
                    "selection": reviewed["packet_selection"],
                },
            },
        ),
    ):
        with pytest.raises(m.CoreError):
            safety._parse_scope(kind, mixed)
    # Human manual creation is still allowed, but validates current scope/budgets.
    for kind, grant_scope, uses in (
        (
            "access",
            {
                "operations": ["muse.open", "muse.read"],
                "target_digests": [scope["open_digest"]],
                "research": scope,
            },
            scope["max_reads"] + 1,
        ),
        ("share", {"packet_digests": [scope["packet_digest"]], "research": scope}, scope["rounds"]),
    ):
        request = m.PermitCreateRequest(
            project_id=reviewed["project_id"],
            kind=kind,
            scope=grant_scope,
            provider="fixture",
            account="fixture",
            issuer="human",
            expires_at=reviewed["expires_at"],
            max_uses=uses,
            request_key=kind,
        )
        assert safety.create_permit(ctx, request).status == "ok"
        with pytest.raises(m.CoreError):
            safety.create_permit(
                ctx, request.model_copy(update={"request_key": kind + "-bad", "max_uses": uses + 1})
            )


def test_manual_mutation_cannot_widen_queued_grant(research_ctx):
    ctx, _, _ = research_ctx
    _, grants = opened(research_ctx)
    accepted = send(research_ctx, grants)
    original = ctx.store.get(m.Permit, grants["share_grant_id"])
    with pytest.raises(m.CoreError):
        with ctx.store.transaction() as conn:
            conn.execute("UPDATE permits SET scope=? WHERE id=?", ("{}", original.id))
    run(ctx)
    assert ctx.store.get(m.Job, accepted.data["job_id"]).state == "succeeded"
    assert ctx.store.get(m.Permit, original.id).scope == original.scope


def test_read_limit_is_enforced_after_reserved_events(research_ctx):
    ctx, project, _ = research_ctx
    _, grants = opened(research_ctx, reads=1)
    discussion = ctx.store.get(m.Discussion, grants["discussion_id"])
    request = {
        "project_id": project,
        "topic": discussion.topic,
        "discussion_id": discussion.id,
        "access_grant_id": grants["access_grant_id"],
        "request_key": "read-one",
    }
    assert app.open_muse_discussion(ctx, request).status == "accepted"
    run(ctx)
    request["request_key"] = "read-two"
    assert app.open_muse_discussion(ctx, request).error.code == m.ErrorCode.SCOPE_DENIED
    assert ctx.store.get(m.Permit, grants["access_grant_id"]).used == 2


def test_unknown_open_reconciles_with_same_pair(research_ctx):
    ctx, _, _ = research_ctx
    reviewed, grants = approved(research_ctx)
    ctx.muse_gateway.mode = "open_timeout"
    accepted = app.open_muse_discussion(
        ctx, {**reviewed["open_request"], "access_grant_id": grants["access_grant_id"]}
    )
    assert accepted.status == "accepted"
    run(ctx)
    discussion = ctx.store.get(m.Discussion, grants["discussion_id"])
    assert discussion.remote_session is None
    refreshed = app.open_muse_discussion(
        ctx,
        {
            **reviewed["open_request"],
            "discussion_id": discussion.id,
            "access_grant_id": grants["access_grant_id"],
            "request_key": "reconcile",
        },
    )
    assert refreshed.status == "accepted"
    run(ctx)
    assert ctx.store.get(m.Discussion, discussion.id).remote_session is not None
    assert ctx.store.get(m.Permit, grants["access_grant_id"]).used == 2
    assert [route for route, _ in ctx.muse_gateway.calls].count("session.start") == 1


def test_provider_account_and_topic_are_loaded_from_durable_state(research_ctx):
    ctx, _, _ = research_ctx
    _, grants = opened(research_ctx)
    discussion = ctx.store.get(m.Discussion, grants["discussion_id"])
    target, payload = read_action(discussion)
    use = _use(discussion, "muse.read", safety.action_digest(target, payload))
    with ctx.store.connection() as conn:
        for changes in (
            {"provider": "other"},
            {"account": "other"},
            {"discussion_id": "dsc_claimed"},
        ):
            with pytest.raises(m.CoreError):
                safety.verify_permit(
                    ctx.store, grants["access_grant_id"], use.model_copy(update=changes), conn=conn
                )


def test_tty_confirmation_decline_and_changed_file_cannot_mint(
    research_ctx, tmp_path, monkeypatch, capsys
):
    import io

    ctx, _, pkt = research_ctx
    reviewed = review(research_ctx)
    path = tmp_path / "open.json"
    path.write_text(json.dumps(reviewed["open_request"]))
    monkeypatch.setattr(app.AppContext, "open", staticmethod(lambda *_: ctx))
    args = [
        "--json",
        "permit",
        "research",
        "--open-request-file",
        str(path),
        "--packet",
        pkt.id,
        "--rounds",
        "2",
        "--max-reads",
        "3",
        "--expires-at",
        reviewed["expires_at"],
        "--request-key",
        "research",
    ]

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr("sys.stdin", Terminal("no\n"))
    assert cli.main(args) == 3
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "E_SCOPE_DENIED"

    class Changed(Terminal):
        def readline(self):
            changed = {**reviewed["open_request"], "topic": "Unreviewed topic"}
            path.write_text(json.dumps(changed))
            return "yes\n"

    monkeypatch.setattr("sys.stdin", Changed())
    assert cli.main(args) == 3
    assert "changed during review" in json.loads(capsys.readouterr().out)["error"]["message"]
    with ctx.store.connection() as conn:
        assert conn.execute("SELECT count(*) FROM permits").fetchone()[0] == 0
    path.write_text(json.dumps(reviewed["open_request"]))
    monkeypatch.setattr("sys.stdin", Terminal("yes\n"))
    assert cli.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["data"]["access_grant_id"] != result["data"]["share_grant_id"]


def test_concurrent_last_question_uses_one_atomic_round(research_ctx):
    ctx, _, _ = research_ctx
    _, grants = opened(research_ctx, rounds=1)
    accepted = send(research_ctx, grants)
    assert accepted.status == "accepted"
    exchange = ctx.store.get(m.Exchange, accepted.data["exchange_id"])
    discussion = ctx.store.get(m.Discussion, exchange.discussion_id)
    prepared = exchange.outbound_payload
    use = _use(
        discussion,
        "muse.send",
        safety.action_digest(prepared.target, prepared.payload),
        exchange.packet_id,
    )

    def reserve():
        try:
            with ctx.store.transaction() as conn:
                safety.verify_permit(
                    ctx.store, grants["share_grant_id"], use, conn=conn, reserve=True
                )
            return True
        except m.CoreError as exc:
            assert exc.detail.code == m.ErrorCode.SCOPE_DENIED
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: reserve(), range(2))) == [False, True]
    assert ctx.store.get(m.Permit, grants["share_grant_id"]).used == 1
