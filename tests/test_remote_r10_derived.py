"""FIXTURE: extractor derivation is distinct from checked transport fidelity."""

from pathlib import Path

from test_remote_media import (  # noqa: F401
    configure,
    egress_guards,
    facebook_page,
    fixture_extractor,
    no_dns,
    remote_request,
    route_checks,
)
from test_remote_r8_unit import accept, run_once
from test_remote_r9_wire import facebook_wire as remote_facebook_wire
from test_remote_r9_wire import origin as remote_origin
from test_remote_r9_wire import resolver, wire  # noqa: F401

from metamuse import models as m
from metamuse import remote_media

origin = remote_origin
facebook_wire = remote_facebook_wire


def test_r10_facebook_embedded_mobile_query_is_a_checked_derived_request(
    ctx, project, facebook_wire
):
    configure(ctx, [("Facebook", ["www.facebook.com"])])
    facebook_wire.routes["watch"] = {
        "status": 200,
        "body": facebook_page("111"),
        "type": "text/html",
    }
    path = "/watch/?v=111&next=https://m.facebook.com/x"
    original = "http://www.facebook.com" + path
    preview = remote_media.preview_remote_media(
        ctx, remote_request(project, original, ["media_metadata"])
    )
    assert preview.destination == original
    _request, _grant, job_id = accept(ctx, project, original)
    run_once(ctx)
    job = ctx.store.get(m.Job, job_id)
    assert job.state == "succeeded", job.error
    assert facebook_wire.wire == [path.replace("://m.facebook.com/", "://www.facebook.com/")]
    doc = (Path(__file__).parents[1] / "docs/dev/COLLECT.md").read_text()
    assert "everywhere in the URL, including query values" in doc
    assert "not the extractor's derivation" in doc
