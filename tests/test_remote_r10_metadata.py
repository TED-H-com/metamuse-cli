"""FIXTURE: extractor/hop provenance never persists credentials or refused URLs."""

import json

import pytest
from test_remote_media import (  # noqa: F401
    configure,
    dump,
    egress_guards,
    facebook_page,
    finish,
    fixture_extractor,
    info_dict,
    issue,
    no_dns,
    remote_request,
    route_checks,
    url_for,
)
from test_remote_media import origin as remote_origin
from test_remote_media import resolver as remote_resolver
from test_remote_r8_unit import accept, run_once

from metamuse import models as m
from metamuse import remote_media

origin = remote_origin
resolver = remote_resolver


@pytest.fixture(autouse=True)
def keep_wal(ctx):
    # Keep WAL pages alive throughout each scan, rather than only inspecting a
    # checkpointed DB after the final connection has removed its WAL.
    with ctx.store.connection():
        yield


def store_bytes(ctx):
    assert ctx.paths.database.with_name(ctx.paths.database.name + "-wal").is_file()
    return b"\n".join(path.read_bytes() for path in ctx.paths.root.rglob("*") if path.is_file())


@pytest.mark.parametrize("kind", ["userinfo", "signed", "fragment", "host", "scheme"])
def test_r10_canonical_candidate_falls_back_without_retaining_refusal(
    ctx, project, origin, capsys, caplog, kind
):
    configure(ctx, [("MetamuseFixture", ["media.fixture.test"])])
    marker = "SYNTHETIC" + "REJECTED" + kind.upper()
    candidates = {
        "userinfo": f"http://user:{marker}@media.fixture.test/clip",
        "signed": f"http://media.fixture.test/clip?sig={marker}",
        "fragment": f"http://media.fixture.test/clip#{marker}",
        "host": f"http://{marker.lower()}.fixture.test/clip",
        "scheme": f"file:///{marker}",
    }
    candidate = candidates[kind]
    info = info_dict(origin.server_address[1], "clip")
    info["webpage_url"] = candidate
    origin.routes["clip"]["body"] = dump(info)
    request = remote_request(project, url_for(origin, "clip"), ["media_metadata", "media_file"])
    request = request.model_copy(
        update={"access_grant_id": issue(ctx, project, request, ["MetamuseFixture"])}
    )
    job = finish(ctx, request)
    assert job.state in ("succeeded", "partial"), job.error
    assert any(path.is_file() for path in (ctx.paths.root / "objects").rglob("*"))
    with ctx.store.connection() as conn:
        source = conn.execute("SELECT * FROM sources WHERE native_id='clip'").fetchone()
        evidence = json.loads(conn.execute("SELECT evidence FROM item_revisions").fetchone()[0])
    assert source["canonical_url"] == url_for(origin, "clip")
    assert json.loads(source["use_basis"])["canonical_basis"] == "extract_url"
    assert evidence["canonical_basis"] == "extract_url"
    captured = capsys.readouterr()
    blob = (
        store_bytes(ctx)
        + captured.out.encode()
        + captured.err.encode()
        + job.model_dump_json().encode()
        + caplog.text.encode()
    )
    assert candidate.encode() not in blob
    assert marker.lower().encode() not in blob.lower()


def test_r10_canonical_candidate_stores_display_and_basis(ctx, project, origin):
    configure(ctx, [("MetamuseFixture", ["media.fixture.test"])])
    info = info_dict(origin.server_address[1], "clip")
    info["webpage_url"] = "http://MEDIA.fixture.test:80/clip/%41?x=%2f"
    origin.routes["clip"]["body"] = dump(info)
    accept(ctx, project, url_for(origin, "clip"))
    run_once(ctx)
    with ctx.store.connection() as conn:
        row = conn.execute(
            "SELECT canonical_url,use_basis FROM sources WHERE native_id='clip'"
        ).fetchone()
        evidence = json.loads(conn.execute("SELECT evidence FROM item_revisions").fetchone()[0])
    assert row["canonical_url"] == "http://media.fixture.test/clip/A?x=%2F"
    assert (
        json.loads(row["use_basis"])["canonical_basis"]
        == evidence["canonical_basis"]
        == "webpage_url"
    )


def test_r10_signed_share_extract_url_falls_back_to_reviewed(
    ctx, project, monkeypatch, capsys, resolver
):
    configure(ctx, [("Facebook", ["www.facebook.com"])])
    original = "https://www.facebook.com/share/v/AAAA/"
    marker = "SYNTHETIC" + "SHARESECRET"
    signed = "https://www.facebook.com/watch/?v=111&sig=" + marker
    resolver.extra["www.facebook.com"] = ["127.0.0.1"]
    monkeypatch.setattr(remote_media, "_page_url", lambda *a: (signed, signed))
    monkeypatch.setattr(
        remote_media,
        "_dial",
        lambda *a: (200, {"Content-Type": "text/html"}, facebook_page("111"), "127.0.0.1", "OK"),
    )
    _request, _grant, job_id = accept(ctx, project, original)
    run_once(ctx)
    job = ctx.store.get(m.Job, job_id)
    assert job.state == "succeeded", job.error
    with ctx.store.connection() as conn:
        source = conn.execute("SELECT * FROM sources").fetchone()
        evidence = json.loads(conn.execute("SELECT evidence FROM item_revisions").fetchone()[0])
    assert source["canonical_url"] == original
    assert (
        json.loads(source["use_basis"])["canonical_basis"]
        == evidence["canonical_basis"]
        == "reviewed"
    )
    captured = capsys.readouterr()
    blob = (
        store_bytes(ctx)
        + captured.out.encode()
        + captured.err.encode()
        + job.model_dump_json().encode()
    )
    assert marker.encode() not in blob and signed.encode() not in blob


@pytest.mark.parametrize("field", ["id", "channel_id", "language"])
def test_r10_upstream_identity_labels_are_bounded_without_echo(ctx, project, origin, field, capsys):
    configure(ctx, [("MetamuseFixture", ["media.fixture.test"])])
    info = info_dict(origin.server_address[1], "clip")
    marker = "SYNTHETIC" + "INVALIDIDENTITY"
    if field == "language":
        info["subtitles"] = {"en?sig=" + marker: info["subtitles"]["en"]}
    else:
        info[field] = "https://user:" + marker + "@media.fixture.test/clip"
    origin.routes["clip"]["body"] = dump(info)
    _request, _grant, job_id = accept(ctx, project, url_for(origin, "clip"))
    run_once(ctx)
    job = ctx.store.get(m.Job, job_id)
    assert job.error["code"] == "E_INPUT_INVALID"
    captured = capsys.readouterr()
    assert marker.encode() not in store_bytes(ctx) + captured.err.encode() + captured.out.encode()


def test_r10_unchanged_title_refreshes_canonical_basis_without_storing_refusal(
    ctx, project, origin
):
    configure(ctx, [("MetamuseFixture", ["media.fixture.test"])])
    first = info_dict(origin.server_address[1], "clip")
    first["webpage_url"] = url_for(origin, "other")
    origin.routes["clip"]["body"] = dump(first)
    accept(ctx, project, url_for(origin, "clip"))
    run_once(ctx)
    first["webpage_url"] = url_for(origin, "clip", "sig=SYNTHETIC_REJECTED_REFRESH")
    origin.routes["clip"]["body"] = dump(first)
    accept(ctx, project, url_for(origin, "clip"))
    run_once(ctx)
    with ctx.store.connection() as conn:
        row = conn.execute("SELECT canonical_url,use_basis FROM sources").fetchone()
        assert conn.execute("SELECT count(*) FROM item_revisions").fetchone()[0] == 1
    assert row["canonical_url"] == url_for(origin, "clip")
    assert json.loads(row["use_basis"])["canonical_basis"] == "extract_url"
    assert b"SYNTHETIC_REJECTED_REFRESH" not in store_bytes(ctx)
