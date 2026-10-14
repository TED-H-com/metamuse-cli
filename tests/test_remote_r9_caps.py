"""FIXTURE: malformed acceptance snapshots preserve the precise fail-closed result."""

import json

import pytest
from test_remote_media import configure, fixture_extractor, remote_request  # noqa: F401
from test_remote_r8_unit import accept, run_once

from metamuse import models as m
from metamuse import remote_media, safety


@pytest.mark.parametrize("edit", ["missing", "extra", "removed"])
def test_r9_snapshot_shape_has_exact_unavailable_error(ctx, project, monkeypatch, edit):
    configure(ctx, [("MetamuseFixture", ["media.fixture.test"])])
    _req, grant, job_id = accept(ctx, project, "https://media.fixture.test/clip")
    with ctx.store.transaction() as conn:
        checkpoint = json.loads(
            conn.execute("SELECT checkpoint FROM job_steps WHERE job_id=?", (job_id,)).fetchone()[0]
        )
        if edit == "removed":
            checkpoint.pop("remote_media_caps")
        elif edit == "extra":
            checkpoint["remote_media_caps"]["max_bytes"] = 1
        else:
            checkpoint["remote_media_caps"].pop("text_max_bytes")
        conn.execute(
            "UPDATE job_steps SET checkpoint=? WHERE job_id=?", (json.dumps(checkpoint), job_id)
        )
    calls = []
    monkeypatch.setattr(remote_media, "_dial", lambda *args: calls.append(args))
    run_once(ctx)
    job = ctx.store.get(m.Job, job_id)
    assert job.state == "awaiting_user"
    assert job.error["code"] == "E_SCOPE_DENIED"
    assert job.error["message"] == "Remote acceptance caps are unavailable"
    assert (
        job.error["next_action"]
        == "Repeat the call without a grant to get a fresh approval preview"
    )
    assert safety.list_permits(ctx, project_id=project).data["permits"][0]["used"] == 0
    assert calls == []
