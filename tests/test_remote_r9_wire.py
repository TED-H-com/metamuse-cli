"""FIXTURE: production curl_cffi/libcurl, checked loopback requests, no external dial."""

from urllib.parse import urlsplit

import pytest
from test_remote_media import (  # noqa: F401
    FixtureHandler,
    configure,
    dump,
    egress_guards,
    facebook_page,
    fixture_extractor,
    info_dict,
    no_dns,
    remote_request,
    route_checks,
)
from test_remote_media import (
    origin as remote_origin,
)
from test_remote_media import (
    resolver as remote_resolver,
)
from test_remote_r8_unit import accept, run_once

from metamuse import app, remote_media, safety
from metamuse import models as m

origin = remote_origin
resolver = remote_resolver


@pytest.fixture
def wire(origin, monkeypatch):
    origin.wire = []
    original = FixtureHandler.do_GET

    def record(self):
        self.server.wire.append(self.path)
        original(self)

    monkeypatch.setattr(FixtureHandler, "do_GET", record)
    return origin


REFUSED = [
    "/clip/%2e%2e/222",
    "/clip/%2E./222",
    "/clip/../222",
    "/clip/./222",
    "/clip/..;x/222",
    "/clip/.;x/222",
    "/clip/.",
    "/clip/..",
    "/../clip",
    "/./clip",
    "/clip?x=%zz",
    "/clip?x=%zz&note=\u3164",
    "/clip?x=%",
    "/clip?x=%0",
    "/clip?v=1&%76=2",
]


@pytest.mark.parametrize("path", REFUSED)
def test_r9_unreviewable_wire_targets_create_nothing(ctx, project, wire, path):
    configure(ctx, [("MetamuseFixture", ["media.fixture.test"])])
    url = f"http://media.fixture.test:{wire.server_address[1]}{path}"
    env = app.submit_collect(ctx, remote_request(project, url, ["media_metadata"]))
    assert env.error.code == m.ErrorCode.INPUT_INVALID
    assert env.error.message == "Remote URL cannot be reviewed safely"
    assert env.approval is None
    assert app.list_jobs(ctx, m.JobListQuery(project_id=project)).data["jobs"] == []
    assert safety.list_permits(ctx, project_id=project).data["permits"] == []
    assert wire.wire == []


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/clip/%41?%76=%7E&x=%5F", "/clip/A?v=~&x=_"),
        ('/clip?x={}|^"<>`', "/clip?x=%7B%7D%7C%5E%22%3C%3E%60"),
        ("/clip/%2f/%26/%e4%b8%ad?x=%2f%26%e4%b8%ad", "/clip/%2F/%26/%E4%B8%AD?x=%2F%26%E4%B8%AD"),
        ("/clip/%2F/%26/%E4%B8%AD?x=%2F%26%E4%B8%AD", "/clip/%2F/%26/%E4%B8%AD?x=%2F%26%E4%B8%AD"),
        ("/clip?note=\u3164\u2800\u034f", "/clip?note=%E3%85%A4%E2%A0%80%CD%8F"),
    ],
)
def test_r9_display_equals_production_wire_request_target(ctx, project, wire, path, expected):
    configure(ctx, [("MetamuseFixture", ["media.fixture.test"])])
    prefix = f"http://media.fixture.test:{wire.server_address[1]}"
    wire.routes[urlsplit(expected).path.strip("/")] = {
        "status": 200,
        "body": dump(info_dict(wire.server_address[1], "clip")),
    }
    original = prefix + path
    preview = remote_media.preview_remote_media(
        ctx, remote_request(project, original, ["media_metadata"])
    )
    assert preview.destination == prefix + expected
    granted, _permit, job_id = accept(ctx, project, original)
    assert wire.wire == []
    run_once(ctx)
    job = ctx.store.get(m.Job, job_id)
    assert job.state == "succeeded", job.error
    assert wire.wire == [expected]
    assert job.payload["inputs"][0]["url"] == granted.inputs[0].url == original
    with ctx.store.connection() as conn:
        assert conn.execute("SELECT original_url FROM sources").fetchone()[0] == original


@pytest.fixture
def facebook_wire(wire, resolver, monkeypatch):
    # Only the destination port is redirected to the synthetic loopback origin.
    # Production _dial, URL options, host, DNS pin and request-target remain real.
    from curl_cffi import CurlOpt
    from curl_cffi._wrapper import ffi, lib

    resolver.extra["www.facebook.com"] = ["127.0.0.1"]
    connect = lib.curl_slist_append(
        ffi.NULL, f"www.facebook.com:80:127.0.0.1:{wire.server_address[1]}".encode()
    )
    original_options = remote_media._options

    def options(opt, checked):
        result = original_options(opt, checked)
        result[CurlOpt.CONNECT_TO] = connect
        return result

    monkeypatch.setattr(remote_media, "_options", options)
    try:
        yield wire
    finally:
        lib.curl_slist_free_all(connect)


@pytest.mark.parametrize(
    "path", ["/watch/?v=111", "/page/videos/111", "/story.php?story_fbid=111&id=5"]
)
def test_r9_real_facebook_page_wire_equals_display(ctx, project, facebook_wire, path):
    wire = facebook_wire
    configure(ctx, [("Facebook", ["www.facebook.com"])])
    wire.routes[urlsplit(path).path.strip("/")] = {
        "status": 200,
        "body": facebook_page("111"),
        "type": "text/html",
    }
    original = "http://www.facebook.com" + path
    preview = remote_media.preview_remote_media(
        ctx, remote_request(project, original, ["media_metadata"])
    )
    assert preview.destination == original
    _req, _grant, job_id = accept(ctx, project, original)
    run_once(ctx)
    job = ctx.store.get(m.Job, job_id)
    assert job.state == "succeeded", job.error
    assert wire.wire == [path]


def test_r9_pinned_normalizer_change_refuses_preview(ctx, project, monkeypatch):
    from yt_dlp.utils import networking

    configure(ctx, [("MetamuseFixture", ["media.fixture.test"])])
    monkeypatch.setattr(networking, "normalize_url", lambda url: url + "?changed=1")
    result = app.submit_collect(
        ctx, remote_request(project, "https://media.fixture.test/clip", ["media_metadata"])
    )
    assert result.error.code == m.ErrorCode.INPUT_INVALID
    assert result.error.message == "Remote URL cannot be reviewed safely"
    assert result.approval is None


def test_r9_pinned_normalizer_change_refuses_step_before_consumption(
    ctx, project, wire, monkeypatch
):
    from yt_dlp.utils import networking

    configure(ctx, [("MetamuseFixture", ["media.fixture.test"])])
    _req, grant, job_id = accept(
        ctx, project, f"http://media.fixture.test:{wire.server_address[1]}/clip"
    )
    monkeypatch.setattr(networking, "normalize_url", lambda url: url + "?changed=1")
    run_once(ctx)
    job = ctx.store.get(m.Job, job_id)
    assert job.error["code"] == "E_INPUT_INVALID"
    assert job.error["message"] == "Remote URL cannot be reviewed safely"
    assert ctx.store.get(m.Permit, grant).used == 0
    assert wire.wire == []
