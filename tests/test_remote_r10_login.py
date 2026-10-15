"""FIXTURE: login walls stop at the real curl transport boundary, loopback only."""

import pytest
from test_remote_media import (  # noqa: F401
    FixtureHandler,
    MetamuseFixtureIE,
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

from metamuse import app, remote_media
from metamuse import models as m


@pytest.mark.parametrize("kind", ["subtitle", "subtitle_redirect", "media_sniff"])
def test_r10_subresource_login_stops_before_later_dials(ctx, project, origin, monkeypatch, kind):
    configure(ctx, [("MetamuseFixture", ["media.fixture.test"])])
    base = url_for(origin, "gate-body")
    info = info_dict(origin.server_address[1], "clip")
    if kind == "media_sniff":
        info["subtitles"] = info["automatic_captions"] = {}
        info["formats"] = [{"url": base, "ext": "wav", "protocol": "http"}]
        resources = ["media_metadata", "media_file"]
    else:
        first = url_for(origin, "gate-hop") if kind.endswith("redirect") else base
        info["subtitles"] = {"en": [{"url": first, "ext": "vtt"}]}
        resources = ["media_metadata", "subtitles", "media_file"]
    origin.routes["clip"]["body"] = dump(info)
    origin.routes["gate-body"] = {
        "status": 200,
        "type": "text/plain" if kind == "media_sniff" else "application/xhtml+xml",
        "body": b'\xef\xbb\xbf \r\n<html><form action="/login/"></form></html>',
    }
    origin.routes["gate-hop"] = {"status": 302, "location": base}
    req = remote_request(project, url_for(origin, "clip"), resources)
    req = req.model_copy(update={"access_grant_id": issue(ctx, project, req, ["MetamuseFixture"])})
    job = finish(ctx, req)
    assert job.state == "awaiting_user" and job.error["code"] == "E_AUTH_REQUIRED", job.error
    expected = ["/clip", "/gate-body"]
    if kind == "subtitle_redirect":
        expected.insert(1, "/gate-hop")
    assert [hit["path"] for hit in origin.hits] == expected
    detail = app.get_job(ctx, m.JobGetRequest(job_id=job.id)).data
    assert detail["steps"][0]["checkpoint"]["phase"] == (
        "subtitles" if kind == "media_sniff" else "metadata"
    )
    with ctx.store.connection() as conn:
        assert conn.execute("SELECT count(*) FROM sources").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM transcript_segments").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM assets").fetchone()[0] == 0


origin = remote_origin


def test_r10_html_subtitle_without_form_remains_partial(ctx, project, origin):
    configure(ctx, [("MetamuseFixture", ["media.fixture.test"])])
    info = info_dict(origin.server_address[1], "clip")
    info["subtitles"] = {"en": [{"url": url_for(origin, "gate-body"), "ext": "vtt"}]}
    info["automatic_captions"] = {}
    origin.routes["clip"]["body"] = dump(info)
    origin.routes["gate-body"] = {
        "status": 200,
        "type": "text/html",
        "body": b"<html>Unavailable subtitle</html>",
    }
    req = remote_request(project, url_for(origin, "clip"), ["media_metadata", "subtitles"])
    req = req.model_copy(update={"access_grant_id": issue(ctx, project, req, ["MetamuseFixture"])})
    job = finish(ctx, req)
    assert job.state == "partial" and "subtitles" in job.missing_fields


def test_r10_public_page_header_login_form_is_left_to_extractor(ctx, project, origin, monkeypatch):
    configure(ctx, [("MetamuseFixture", ["media.fixture.test"])])
    origin.routes["clip"] = {
        "status": 200,
        "type": "text/html",
        "body": b'<html><form action="/login/">Header login</form><article>Public</article></html>',
    }

    def extract(self, url):
        self._download_webpage(url, "clip", note=False)
        return info_dict(origin.server_address[1], "clip")

    monkeypatch.setattr(MetamuseFixtureIE, "_real_extract", extract)
    req = remote_request(project, url_for(origin, "clip"), ["media_metadata"])
    req = req.model_copy(update={"access_grant_id": issue(ctx, project, req, ["MetamuseFixture"])})
    assert finish(ctx, req).state == "succeeded"


def test_r10_login_parser_checks_every_block_and_detects_late_split_form():
    checks = []
    payload = b"<html>" + b" " * 65520 + b'<form action="/login/"></form></html>'
    assert remote_media._login_form(
        payload, "https://media.fixture.test/clip", check_deadline=lambda: checks.append(True)
    )
    assert len(checks) == (len(payload) + 32767) // 32768 + 1
