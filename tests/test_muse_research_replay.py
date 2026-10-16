"""Replay existing research receipts without renewing permission or opening again."""

import copy
import json
from datetime import datetime, timedelta

import pytest
from test_muse_lifecycle import run
from test_muse_research import opened, review
from test_muse_research import research_ctx as research_ctx  # noqa: F401

from metamuse import _muse_research as research
from metamuse import app, cli, safety
from metamuse import models as m
from metamuse.config import load_config


def counts(ctx):
    with ctx.store.connection() as conn:
        return tuple(
            conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "permits",
                "events",
                "request_receipts",
                "jobs",
                "discussions",
                "exchanges",
            )
        )


def prepare(ctx, reviewed, **changes):
    options = {
        "packet_id": reviewed["scope"]["packet_id"],
        "rounds": reviewed["scope"]["rounds"],
        "max_reads": reviewed["scope"]["max_reads"],
        "expires_at": reviewed["expires_at"],
        "request_key": reviewed["request_key"],
        **changes,
    }
    with ctx.store.connection() as conn:
        return research.prepare_research(ctx, reviewed["open_request"], **options, conn=conn)


def test_identical_pair_replay_after_bound_open(research_ctx):
    ctx, _, _ = research_ctx
    reviewed = review(research_ctx)
    grants = research.issue_research(ctx, reviewed).data
    before_open = research.issue_research(ctx, reviewed).data
    assert before_open["replayed"]
    assert before_open["access_grant_id"] == grants["access_grant_id"]
    assert before_open["share_grant_id"] == grants["share_grant_id"]
    opened = app.open_muse_discussion(
        ctx, {**reviewed["open_request"], "access_grant_id": grants["access_grant_id"]}
    )
    assert opened.status == "accepted"
    run(ctx)
    access = ctx.store.get(m.Permit, grants["access_grant_id"])
    share = ctx.store.get(m.Permit, grants["share_grant_id"])
    assert access.used == 1 and share.used == 0
    before = counts(ctx)
    calls = list(ctx.muse_gateway.calls)

    replay = research.issue_research(ctx, reviewed).data

    assert replay["replayed"]
    assert replay["access_grant_id"] == grants["access_grant_id"]
    assert replay["share_grant_id"] == grants["share_grant_id"]
    assert replay["access"] == access.model_dump(mode="json")
    assert replay["share"] == share.model_dump(mode="json")
    assert replay["approval_digest"] == grants["approval_digest"]
    assert replay["discussion_id"] == grants["discussion_id"]
    assert counts(ctx) == before
    assert ctx.muse_gateway.calls == calls


def test_cli_replays_original_review_after_bound_open(research_ctx, tmp_path, monkeypatch, capsys):
    ctx, _, pkt = research_ctx
    reviewed = review(research_ctx)
    grants = research.issue_research(ctx, reviewed).data
    private = copy.deepcopy(reviewed)
    private["open_request"]["topic"] = "curl -b SYNTHETIC_PRIVATE_REPLAY_742"
    before = counts(ctx)
    with pytest.raises(m.CoreError) as raised:
        prepare(ctx, private)
    assert raised.value.detail.code == m.ErrorCode.INPUT_INVALID
    assert "SYNTHETIC_PRIVATE_REPLAY_742" not in str(raised.value)
    assert counts(ctx) == before and not ctx.muse_gateway.calls
    assert (
        app.open_muse_discussion(
            ctx, {**reviewed["open_request"], "access_grant_id": grants["access_grant_id"]}
        ).status
        == "accepted"
    )
    run(ctx)
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
        reviewed["request_key"],
        "--exact-digest",
        grants["approval_digest"],
    ]
    before, calls = counts(ctx), list(ctx.muse_gateway.calls)
    assert cli.main(args) == 0
    result = json.loads(capsys.readouterr().out)["data"]
    assert result["replayed"] and result["access"]["used"] == 1
    assert result["access_grant_id"] == grants["access_grant_id"]
    assert result["share_grant_id"] == grants["share_grant_id"]
    assert result["approval_digest"] == grants["approval_digest"]
    args[args.index("--rounds") + 1] = "1"
    assert cli.main(args) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "E_IDEMPOTENCY_CONFLICT"
    changed = copy.deepcopy(reviewed)
    changed["scope"]["rounds"] = changed["share_max_uses"] = 1
    args[-1] = m.payload_digest(changed)
    assert cli.main(args) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "E_IDEMPOTENCY_CONFLICT"
    assert counts(ctx) == before and ctx.muse_gateway.calls == calls


@pytest.mark.parametrize("change", ["scope", "wire", "expiry"])
def test_changed_review_conflicts_after_bound_open(research_ctx, change):
    ctx, _, _ = research_ctx
    reviewed, grants = opened(research_ctx)
    changed = copy.deepcopy(reviewed)
    if change == "scope":
        changed["scope"]["rounds"] = 1
        changed["share_max_uses"] = 1
    elif change == "wire":
        changed["open_payload"]["body"]["title"] = "Unreviewed synthetic topic"
    else:
        changed["expires_at"] = (
            datetime.fromisoformat(reviewed["expires_at"]) + timedelta(minutes=1)
        ).isoformat()
    before, calls = counts(ctx), list(ctx.muse_gateway.calls)
    with pytest.raises(m.CoreError) as raised:
        research.issue_research(ctx, changed)
    assert raised.value.detail.code == m.ErrorCode.IDEMPOTENCY_CONFLICT
    assert counts(ctx) == before and ctx.muse_gateway.calls == calls
    assert ctx.store.get(m.Permit, grants["access_grant_id"]).used == 1


def test_replay_retains_expired_revoked_stale_metadata_without_authority(research_ctx):
    ctx, _, pkt = research_ctx
    reviewed, grants = opened(research_ctx)
    for kind in ("access", "share"):
        safety.revoke_permit(ctx, grants[kind + "_grant_id"], request_key="revoke-" + kind)
    with ctx.store.transaction() as conn:
        conn.execute("UPDATE packets SET stale=1 WHERE id=?", (pkt.id,))
    later = (datetime.fromisoformat(reviewed["expires_at"]) + timedelta(hours=1)).isoformat()
    ctx.clock = ctx.store.clock = lambda: later
    path = ctx.paths.root / "metamuse.toml"
    path.write_text(path.read_text().replace("enabled = true", "enabled = false"))
    ctx.policy = load_config(ctx.paths.root)
    before, calls = counts(ctx), list(ctx.muse_gateway.calls)

    assert prepare(ctx, reviewed) == reviewed
    replay = research.issue_research(ctx, reviewed).data
    assert replay["replayed"] and replay["access"]["used"] == 1 and replay["share"]["used"] == 0
    for kind in ("access", "share"):
        assert replay[kind + "_grant_id"] == grants[kind + "_grant_id"]
        assert replay[kind] == ctx.store.get(m.Permit, grants[kind + "_grant_id"]).model_dump(
            mode="json"
        )
        assert replay[kind]["revoked"] and replay[kind]["expires_at"] == reviewed["expires_at"]
    fresh = {**reviewed, "request_key": "no-new-authority"}
    with pytest.raises(m.CoreError) as raised:
        research.issue_research(ctx, fresh)
    assert raised.value.detail.code == m.ErrorCode.SCOPE_DENIED
    ctx.non_interactive = True
    for action in (lambda: research.issue_research(ctx, reviewed), lambda: prepare(ctx, reviewed)):
        with pytest.raises(m.CoreError) as raised:
            action()
        assert raised.value.detail.code == m.ErrorCode.SCOPE_REQUIRED
    assert counts(ctx) == before and ctx.muse_gateway.calls == calls


@pytest.mark.parametrize("route", ["prepare", "issue"])
def test_disappearing_receipt_cannot_issue_without_preflight(research_ctx, monkeypatch, route):
    ctx, _, _ = research_ctx
    reviewed, grants = opened(research_ctx)
    original = research._receipt
    before, calls = counts(ctx), list(ctx.muse_gateway.calls)
    removed = False

    def disappear(conn, key):
        nonlocal removed
        receipt = original(conn, key)
        if receipt is not None and not removed:
            assert not conn.in_transaction
            removed = True
            with ctx.store.transaction() as writer:
                assert (
                    writer.execute(
                        "DELETE FROM request_receipts "
                        "WHERE operation='permit.research' AND request_key=?",
                        (key,),
                    ).rowcount
                    == 1
                )
        return receipt

    monkeypatch.setattr(research, "_receipt", disappear)
    with pytest.raises(m.CoreError) as raised:
        if route == "prepare":
            prepare(ctx, reviewed)
        else:
            research.issue_research(ctx, reviewed)
    assert removed and raised.value.detail.code == m.ErrorCode.SCOPE_DENIED
    expected = list(before)
    expected[2] -= 1  # Only the deliberately removed receipt changes.
    assert counts(ctx) == tuple(expected) and ctx.muse_gateway.calls == calls
    assert ctx.store.get(m.Permit, grants["access_grant_id"]).used == 1
    assert ctx.store.get(m.Permit, grants["share_grant_id"]).used == 0


def test_json_approval_flag_cannot_replace_new_process_preflight(research_ctx):
    ctx, _, _ = research_ctx
    reviewed = {**review(research_ctx), "approved": True, "preflight": True}
    before = counts(ctx)
    with pytest.raises(m.CoreError) as raised:
        research.issue_research(ctx, reviewed)
    assert raised.value.detail.code == m.ErrorCode.SCOPE_DENIED
    assert counts(ctx) == before and not ctx.muse_gateway.calls
