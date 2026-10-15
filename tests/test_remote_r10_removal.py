"""FIXTURE: changed subtitle bytes retire omitted evidence atomically."""

import json

import pytest
from test_remote_media import (  # noqa: F401
    configure,
    dump,
    egress_guards,
    finish,
    fixture_extractor,
    info_dict,
    issue,
    no_dns,
    remote_request,
    resolver,
    route_checks,
    url_for,
)
from test_remote_media import origin as remote_origin

from metamuse import app, content
from metamuse import models as m
from metamuse.store import TransactionWriter

origin = remote_origin


def vtt(count):
    return (
        "WEBVTT\n\n"
        + "\n".join(
            f"00:00:0{i}.000 --> 00:00:0{i + 1}.000\nretirement cue {i}\n" for i in range(count)
        )
    ).encode()


def collect(ctx, project, origin, payload):
    configure(ctx, [("MetamuseFixture", ["media.fixture.test"])])
    info = info_dict(origin.server_address[1], "clip")
    info["automatic_captions"] = {}
    origin.routes["clip"]["body"] = dump(info)
    origin.subtitles[("clip", "provided")] = payload
    req = remote_request(project, url_for(origin, "clip"), ["media_metadata", "subtitles"])
    req = req.model_copy(update={"access_grant_id": issue(ctx, project, req, ["MetamuseFixture"])})
    return finish(ctx, req)


def cue_rows(ctx):
    with ctx.store.connection() as conn:
        return [
            dict(row)
            for row in conn.execute("SELECT * FROM transcript_segments ORDER BY cue_index,revision")
        ]


def search(ctx, project, query):
    return app.search_evidence(ctx, {"project_id": project, "query": query}).data["results"]


def resolve(ctx, project, ref):
    with ctx.store.connection() as conn:
        return content._Resolver(ctx, project, conn).resolve(m.EvidenceRef.model_validate(ref))


def test_r10_shorter_track_removes_current_cue_and_stales_derivatives(ctx, project, origin):
    assert collect(ctx, project, origin, vtt(3)).state == "succeeded"
    third = cue_rows(ctx)[2]
    ref = {"kind": "transcript_segment", "id": third["id"], "revision": 1}
    packet = app.prepare_packet(
        ctx, {"project_id": project, "selection": [ref], "request_key": "packet"}
    )
    draft = app.save_draft(
        ctx,
        {
            "project_id": project,
            "text": "Host draft",
            "platform": "generic",
            "facts_refs": [ref],
            "request_key": "draft",
        },
    )
    assert packet.status == draft.status == "ok"
    assert collect(ctx, project, origin, vtt(2)).state == "succeeded"
    assert search(ctx, project, "retirement cue 2") == []
    removed = ctx.store.get(m.TranscriptSegment, third["id"])
    assert removed.revision == 2 and removed.removed and removed.text == ""
    assert (removed.start_ms, removed.end_ms) == (third["start_ms"], third["end_ms"])
    assert ctx.store.get(m.Packet, packet.data["id"]).stale
    assert ctx.store.get(m.Draft, draft.data["draft_id"]).stale
    old = resolve(ctx, project, ref)
    assert old["stale"] is True and old["text"] == third["text"]
    current = {**ref, "revision": removed.revision}
    with pytest.raises(m.CoreError) as held:
        resolve(ctx, project, current)
    assert held.value.detail.code == m.ErrorCode.REFERENCE_INVALID
    assert (
        held.value.detail.message
        == "A later source revision removed the cue; last text-bearing revision: 1"
    )
    for fmt in ("bundle", "jsonl", "markdown", "text", "csv"):
        response = app.export_objects(
            ctx,
            {
                "project_id": project,
                "selection": [current],
                "format": fmt,
                "request_key": "export-" + fmt,
            },
        )
        assert response.error.code == m.ErrorCode.REFERENCE_INVALID
        assert third["text"] not in response.model_dump_json()
    assert (
        app.prepare_packet(
            ctx, {"project_id": project, "selection": [current], "request_key": "removed"}
        ).error.code
        == m.ErrorCode.REFERENCE_INVALID
    )


@pytest.mark.parametrize(
    "replacement,complete",
    [
        (b"WEBVTT\n\n", True),
        (b"WEBVTT\n\nnot a cue\n", False),
        (b"not a subtitle", False),
        (b"\xff", False),
    ],
)
def test_r10_empty_or_unreadable_replacement_retires_all(
    ctx, project, origin, replacement, complete
):
    collect(ctx, project, origin, vtt(3))
    job = collect(ctx, project, origin, replacement)
    assert job.state == "partial" and "subtitles" in job.missing_fields
    assert search(ctx, project, "retirement cue") == []
    rows = cue_rows(ctx)
    assert len(rows) == 6 and sum(row["removed"] for row in rows) == 3
    with ctx.store.connection() as conn:
        basis = json.loads(
            conn.execute(
                "SELECT use_basis FROM sources WHERE native_id LIKE '%:subtitle:%'"
            ).fetchone()[0]
        )
    assert (
        basis["revision"] == 2 and basis["cue_count"] == 0 and bool(basis["complete"]) is complete
    )


def test_r10_reappearance_and_identical_refetch_are_idempotent(ctx, project, origin):
    collect(ctx, project, origin, vtt(3))
    third_id = cue_rows(ctx)[2]["id"]
    collect(ctx, project, origin, vtt(2))
    collect(ctx, project, origin, vtt(3))
    restored = ctx.store.get(m.TranscriptSegment, third_id)
    assert restored.revision == 3 and not restored.removed
    assert search(ctx, project, "retirement cue 2")[0]["ref"]["id"] == third_id
    before = cue_rows(ctx)
    collect(ctx, project, origin, vtt(3))
    assert cue_rows(ctx) == before


def test_r10_removal_failure_rolls_back_cues_basis_and_stale_flags(
    ctx, project, origin, monkeypatch
):
    collect(ctx, project, origin, vtt(3))
    before = cue_rows(ctx)
    third = before[2]
    ref = {"kind": "transcript_segment", "id": third["id"], "revision": 1}
    packet = app.prepare_packet(
        ctx, {"project_id": project, "selection": [ref], "request_key": "packet"}
    ).data["id"]
    draft = app.save_draft(
        ctx,
        {
            "project_id": project,
            "text": "Host draft",
            "platform": "generic",
            "facts_refs": [ref],
            "request_key": "draft",
        },
    ).data["draft_id"]
    with ctx.store.connection() as conn:
        basis_before = conn.execute(
            "SELECT use_basis FROM sources WHERE native_id LIKE '%:subtitle:%'"
        ).fetchone()[0]
    original = TransactionWriter.insert
    injected = []

    def insert(self, record):
        result = original(self, record)
        if isinstance(record, m.TranscriptSegment) and getattr(record, "removed", False):
            injected.append(True)
            raise m.CoreError(m.ErrorCode.STORAGE, "Synthetic failure after removal insert")
        return result

    monkeypatch.setattr(TransactionWriter, "insert", insert)
    collect(ctx, project, origin, vtt(2))
    assert injected == [True]
    assert cue_rows(ctx) == before
    assert not ctx.store.get(m.Packet, packet).stale
    assert not ctx.store.get(m.Draft, draft).stale
    with ctx.store.connection() as conn:
        assert (
            conn.execute(
                "SELECT use_basis FROM sources WHERE native_id LIKE '%:subtitle:%'"
            ).fetchone()[0]
            == basis_before
        )


def test_r10_failed_fetch_and_first_empty_track_do_not_retire_or_create(ctx, project, origin):
    collect(ctx, project, origin, b"WEBVTT\n\n")
    assert cue_rows(ctx) == []
    with ctx.store.connection() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM sources WHERE native_id LIKE '%:subtitle:%'"
            ).fetchone()[0]
            == 0
        )
    collect(ctx, project, origin, vtt(3))
    before = cue_rows(ctx)
    origin.denied_subs.add(("clip", "provided"))
    assert collect(ctx, project, origin, vtt(2)).error["code"] == "E_ACCESS_DENIED"
    assert cue_rows(ctx) == before
