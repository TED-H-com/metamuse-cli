"""Synthetic records shared only by the core F1–F9 review regressions."""

import io

import pytest

from metamuse import app
from metamuse import models as m
from metamuse import store as core


@pytest.fixture
def records(ctx, project):
    src = ctx.store.insert(
        m.Source(
            id="src_review",
            project_id=project,
            platform="generic",
            collection_method="file_import",
            fetched_at=ctx.clock(),
        )
    )
    itm = ctx.store.insert(
        m.Item(id="itm_review", project_id=project, source_id=src.id, kind="post")
    )
    ctx.store.insert(
        m.ItemRevision(
            item_id=itm.id,
            revision=1,
            text="synthetic",
            text_hash=m.payload_digest("synthetic"),
            fetched_at=ctx.clock(),
        )
    )
    asset = ctx.store.put_object(io.BytesIO(b"synthetic bytes"), mime="text/plain")
    ctx.store.insert(
        m.ItemAsset(item_id=itm.id, revision=1, asset_id=asset.id, relation="attachment")
    )
    ref = {"kind": "item_revision", "id": itm.id, "revision": 1}
    packet = app.prepare_packet(
        ctx, {"project_id": project, "selection": [ref], "request_key": "packet"}
    ).data
    saved = app.save_draft(
        ctx,
        {
            "project_id": project,
            "text": "synthetic draft",
            "platform": "generic",
            "request_key": "draft",
        },
    ).data
    req = {"project_id": project, "selection": [ref], "format": "jsonl", "request_key": "export"}
    exported = app.export_objects(ctx, req).data
    permit = ctx.store.insert(
        m.Permit(
            id="pmt_review",
            project_id=project,
            kind="share",
            scope={},
            issuer="fixture",
            issued_at=ctx.clock(),
            expires_at="2099-01-01T00:00:00Z",
            max_uses=2,
            used=1,
            revoked=True,
        )
    )
    discussion = ctx.store.insert(
        m.Discussion(
            id="dsc_review",
            project_id=project,
            provider="fixture",
            auth_ref="fixture",
            topic="fixture",
        )
    )
    ctx.store.insert(
        m.Exchange(
            id="exc_review",
            project_id=project,
            discussion_id=discussion.id,
            request_key="exchange",
            question="fixture",
            packet_id=packet["id"],
            digest=m.payload_digest("e"),
            permit_id=permit.id,
            outcome="answered",
        )
    )
    job = ctx.store.insert(
        m.Job(
            id="job_review",
            project_id=project,
            operation="fixture",
            request_key="job",
            payload_digest=m.payload_digest({}),
            payload={},
            created_at=ctx.clock(),
            updated_at=ctx.clock(),
        )
    )
    ctx.store.insert(
        m.JobStep(
            id="stp_review",
            job_id=job.id,
            sequence=0,
            target={},
            state="send_uncertain",
            attempts=1,
            side_effect_class="external_submit",
        )
    )
    ctx.store.rotate_workspace_key(core.CONTENT_CURSOR_KEY)
    return {"asset": asset, "export": exported, "request": req, "draft": saved}
