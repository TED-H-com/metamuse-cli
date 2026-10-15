"""Decisive research boundaries against the named privacy integration."""

import hashlib
import json

import pytest
from test_muse_research import opened, review, send
from test_muse_research import research_ctx as research_ctx  # noqa: F401

from metamuse import _muse_research as research
from metamuse import app, cli
from metamuse import models as m


def test_research_topic_refused_before_human_review(research_ctx, tmp_path, monkeypatch, capsys):
    ctx, _, pkt = research_ctx
    reviewed = review(research_ctx)
    request = {**reviewed["open_request"], "topic": "Password: SYNTHETIC_PRIVATE_742"}
    path = tmp_path / "open.json"
    path.write_text(json.dumps(request))
    monkeypatch.setattr(app.AppContext, "open", staticmethod(lambda *_: ctx))
    monkeypatch.setattr(research, "preview", lambda *_: pytest.fail("Private topic reached review"))
    assert (
        cli.main(
            [
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
                "private-topic",
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["error"]["code"] == "E_INPUT_INVALID" and result["approval"] is None
    assert "SYNTHETIC_PRIVATE_742" not in captured.out + captured.err
    with ctx.store.connection() as conn:
        assert conn.execute("SELECT count(*) FROM permits").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM discussions").fetchone()[0] == 0


def test_bounded_question_refused_before_persistence_and_consumption(research_ctx):
    ctx, _, _ = research_ctx
    _, grants = opened(research_ctx)
    result = send(research_ctx, grants, question="curl -b SYNTHETIC_PRIVATE_742")
    assert result.error.code == m.ErrorCode.INPUT_INVALID and result.approval is None
    assert "SYNTHETIC_PRIVATE_742" not in result.model_dump_json()
    assert ctx.store.get(m.Permit, grants["share_grant_id"]).used == 0
    with ctx.store.connection() as conn:
        assert conn.execute("SELECT count(*) FROM exchanges").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    assert [call[0] for call in ctx.muse_gateway.calls] == ["session.start"]


def test_research_packet_requires_current_preview_fixpoint(research_ctx):
    ctx, project, pkt = research_ctx
    text = "Password: SYNTHETIC_PRIVATE_742"
    legacy = pkt.model_copy(
        update={
            "id": "pkt_legacy_private",
            "rendered_text": text,
            "digest": hashlib.sha256(text.encode()).hexdigest(),
        }
    )
    ctx.store.insert(legacy)
    with pytest.raises(m.CoreError) as raised:
        review((ctx, project, legacy))
    assert raised.value.detail.code == m.ErrorCode.REFERENCE_INVALID
    assert "SYNTHETIC_PRIVATE_742" not in str(raised.value)
    with ctx.store.connection() as conn:
        assert conn.execute("SELECT count(*) FROM permits").fetchone()[0] == 0
