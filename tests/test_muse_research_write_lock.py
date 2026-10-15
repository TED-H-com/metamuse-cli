"""Research privacy preflight and atomic issuance use the real Store boundaries."""

import contextlib
import hashlib
import json

import pytest
from test_b16a_round7_lock import instrument_locks
from test_muse_lifecycle import run
from test_muse_research import research_ctx as research_ctx  # noqa: F401
from test_muse_research import review, send

from metamuse import _muse_research as research
from metamuse import app, safety
from metamuse import models as m


def manual_request(reviewed, kind="share"):
    scope = reviewed["scope"]
    grant_scope = (
        {
            "operations": ["muse.open", "muse.read"],
            "target_digests": [scope["open_digest"]],
        }
        if kind == "access"
        else {"packet_digests": [scope["packet_digest"]]}
    )
    return m.PermitCreateRequest(
        project_id=reviewed["project_id"],
        kind=kind,
        scope={**grant_scope, "research": scope},
        provider=reviewed["provider"],
        account=reviewed["account"],
        issuer="local_operator",
        expires_at=reviewed["expires_at"],
        max_uses=reviewed[kind + "_max_uses"],
        request_key="manual-" + kind,
    )


@pytest.mark.parametrize("route", ["pair", "manual-access", "manual-share"])
def test_issuance_replay_and_verification_never_redact_under_writer(
    research_ctx, monkeypatch, route
):
    ctx, project, pkt = research_ctx
    reviewed = review(research_ctx)
    calls = instrument_locks(ctx, monkeypatch)
    if route == "pair":
        grants = research.issue_research(ctx, reviewed).data
        replay = research.issue_research(ctx, reviewed).data
        assert replay["replayed"] and replay["access_grant_id"] == grants["access_grant_id"]
        assert replay["share_grant_id"] == grants["share_grant_id"]
        opened = app.open_muse_discussion(
            ctx, {**reviewed["open_request"], "access_grant_id": grants["access_grant_id"]}
        )
        assert opened.status == "accepted"
        run(ctx)
        submitted = send(research_ctx, grants)
        assert submitted.status == "accepted"
        run(ctx)
        read = app.read_muse_exchange(
            ctx,
            {
                "exchange_id": submitted.data["exchange_id"],
                "refresh": True,
                "access_grant_id": grants["access_grant_id"],
            },
        )
        assert read.status == "accepted"
        run(ctx)
        assert ctx.store.get(m.Permit, grants["access_grant_id"]).used == 2
        assert ctx.store.get(m.Permit, grants["share_grant_id"]).used == 1
        assert ctx.store.get(m.Job, read.data["refresh_job_id"]).state == "succeeded"
    else:
        request = manual_request(reviewed, route.removeprefix("manual-"))
        result = safety.create_permit(ctx, request)
        assert result.status == "ok"
        safety.revoke_permit(ctx, result.data["id"], request_key="revoke-manual")
        before = len(calls)
        replay = safety.create_permit(ctx, request).data
        assert replay["id"] == result.data["id"] and replay["replayed"] and replay["revoked"]
        assert replay["used"] == 0 and replay["expires_at"] == result.data["expires_at"]
        assert len(calls) == before  # Receipt replay has no new privacy work or authority.
    assert any(
        row["binding"] == "lifecycle.redact_text"
        and row["chars"] == len(reviewed["scope"]["topic"])
        for row in calls
    ), calls
    assert any(
        row["binding"] == "safety.redact_text" and row["chars"] == len(pkt.rendered_text)
        for row in calls
    ), calls
    locked = [row for row in calls if row["in_write_txn"]]
    print(
        "RESEARCH_WRITE_REDACTION "
        + json.dumps({"route": route, "calls": len(calls), "locked": locked}, sort_keys=True)
    )
    assert not locked, calls


@pytest.mark.parametrize("route", ["pair", "manual"])
@pytest.mark.parametrize("mutation", ["packet", "topic", "config", "revision"])
def test_preflight_to_writer_change_refuses_atomically(research_ctx, monkeypatch, route, mutation):
    ctx, _, pkt = research_ctx
    reviewed = review(research_ctx)
    request = manual_request(reviewed)
    transaction = ctx.store.transaction

    def counts():
        with ctx.store.connection() as conn:
            return tuple(
                conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("permits", "events", "request_receipts")
            )

    before = counts()
    pending = True

    @contextlib.contextmanager
    def mutate_before_writer(*args, **kwargs):
        nonlocal pending
        if pending:
            pending = False
            if mutation == "packet":
                text = pkt.rendered_text + "\nChanged synthetic packet bytes."
                digest = hashlib.sha256(text.encode()).hexdigest()
                with transaction() as conn:
                    # Inject out-of-band corruption into this synthetic database;
                    # normal Store writes cannot change immutable packet bytes.
                    trigger = conn.execute(
                        "SELECT sql FROM sqlite_master WHERE type='trigger' "
                        "AND name='packet_payload_immutable'"
                    ).fetchone()[0]
                    conn.execute("DROP TRIGGER packet_payload_immutable")
                    conn.execute(
                        "UPDATE packets SET rendered_text=?,digest=? WHERE id=?",
                        (text, digest, pkt.id),
                    )
                    conn.execute(trigger)
                changed = ctx.store.get(m.Packet, pkt.id)
                assert changed.rendered_text == text != pkt.rendered_text
                assert changed.digest == digest != pkt.digest
            elif mutation == "topic":
                if route == "pair":
                    reviewed["open_request"]["topic"] = "Unreviewed synthetic topic"
                else:
                    request.scope["research"]["topic"] = "Unreviewed synthetic topic"
            elif mutation == "config":
                path = ctx.paths.root / "metamuse.toml"
                text = path.read_text()
                assert "max_exchanges = 3" in text
                path.write_text(text.replace("max_exchanges = 3", "max_exchanges = 4"))
            else:
                old = ctx.store.get(m.ItemRevision, pkt.selection[0].id)
                ctx.store.insert(old.model_copy(update={"revision": old.revision + 1}))
        with transaction(*args, **kwargs) as conn:
            yield conn

    monkeypatch.setattr(ctx.store, "transaction", mutate_before_writer)
    with pytest.raises(m.CoreError) as raised:
        if route == "pair":
            research.issue_research(ctx, reviewed)
        else:
            safety.create_permit(ctx, request)
    assert not pending  # The mutation occurred after preflight, at actual writer entry.
    assert raised.value.detail.code == m.ErrorCode.SCOPE_DENIED
    assert counts() == before


def test_serialized_preflight_cannot_authorize_manual_creation(research_ctx):
    ctx, _, _ = research_ctx
    reviewed = review(research_ctx)
    request = manual_request(reviewed)
    scope = safety._parse_scope(request.kind, request.scope).research
    with ctx.store.transaction() as conn, pytest.raises(m.CoreError) as raised:
        research.validate_creation(
            ctx,
            conn,
            scope,
            request,
            preflight={
                **reviewed,
                "prechecked": True,
            },
        )
    assert raised.value.detail.code == m.ErrorCode.SCOPE_DENIED
    with ctx.store.connection() as conn:
        assert conn.execute("SELECT count(*) FROM permits").fetchone()[0] == 0
