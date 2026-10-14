"""B13 post share links: synthetic TLS loopback Chromium integration, never LIVE."""

import json

import pytest
from test_facebook import _synthetic_post_html
from test_facebook_review import URL, browser_fixture, crash_after_batch, finish, submit

from metamuse import facebook
from metamuse import models as m

SHARE = "https://www.facebook.com/share/p/SyntheticCode1/"
SHARE_PATH = "/share/p/SyntheticCode1/"
POST_PATH = "/synth.lab/posts/pfbid0101synth"


@pytest.fixture(autouse=True)
def reset_seams():
    yield
    facebook.set_driver_factory(None)
    facebook.set_egress_factory(None)
    facebook.set_secret_store_factory(None)
    facebook.set_resolver_factory(None)
    facebook.set_batch_hook(None)


@pytest.mark.parametrize("host", facebook.PRIMARY_FACEBOOK_HOSTS)
def test_share_normalization_and_permit_digest_bind_the_share_url(host):
    target = facebook.parse_post_permalink(f"https://{host}/share/p/SyntheticCode1/")
    assert target.canonical_url == SHARE
    assert target.is_share and target.page_type == "post_permalink"
    digest = facebook.permit_digest(
        project_id="prj_synthetic",
        account="acct_synth",
        url=target.canonical_url,
        resources=["post", "comments"],
    )
    assert digest == facebook.permit_digest(
        project_id="prj_synthetic", account="acct_synth", url=SHARE, resources=["post", "comments"]
    )
    assert digest != facebook.permit_digest(
        project_id="prj_synthetic", account="acct_synth", url=URL, resources=["post", "comments"]
    )


@pytest.mark.parametrize(
    "path",
    [
        "/share/r/abc/",
        "/share/v/abc/",
        "/share/x/abc/",
        "/reel/abc/",
        "/watch",
        "/videos/abc/",
        "/groups/1/posts/2/",
        "/share/p/abc/?q=1",
        "/share/p/abc/#fragment",
        "/share/p/abc/#",
        "/share/p/a_b/",
        "/share/p/" + "a" * 65 + "/",
    ],
)
def test_unsupported_share_or_media_kind_has_remote_media_guidance(path):
    with pytest.raises(m.CoreError) as error:
        facebook.parse_post_permalink("https://www.facebook.com" + path)
    assert error.value.detail.code == m.ErrorCode.UNSUPPORTED
    assert "remote-media (B12)" in error.value.detail.next_action
    assert "group post URLs are unsupported" in error.value.detail.next_action


@pytest.mark.parametrize("hop", ["302", "script", "meta"])
def test_share_resolution_records_requested_and_resolved_provenance(tmp_path, monkeypatch, hop):
    final = POST_PATH + "?track=synthetic#anchor"
    initial = (
        (302, final)
        if hop == "302"
        else (
            f"<script>location.replace('{final}')</script>"
            if hop == "script"
            else f'<meta http-equiv="refresh" content="0;url={final}">'
        )
    )
    pages = {SHARE_PATH: initial, POST_PATH + "?track=synthetic": _synthetic_post_html()}
    with browser_fixture(tmp_path, monkeypatch, pages, url=SHARE) as env:
        ctx, project, permit, _, hits, _, _ = env
        job = finish(ctx, submit(ctx, project, permit, url=SHARE))
        assert job["coverage_status"] == "scope_satisfied", job
        data = job["result"]["data"]["steps"][0]["data"]
        assert data["requested_locator"] == SHARE
        assert data["resolved_permalink"] == URL
        with ctx.store.connection() as conn:
            row = conn.execute(
                "SELECT original_url,canonical_url,use_basis FROM sources"
            ).fetchone()
            assert row[0] == SHARE and row[1] == URL
            basis = json.loads(row[2])
            assert basis["requested_locator"] == SHARE
            assert basis["resolved_permalink"] == URL
            assert conn.execute("SELECT used FROM permits WHERE id=?", (permit,)).fetchone()[0] == 1
        assert any(path == SHARE_PATH for path, _ in hits)


@pytest.mark.parametrize(
    "destination,code",
    [
        ("/reel/synthetic/", "E_UNSUPPORTED"),
        ("/groups/1/posts/2/", "E_UNSUPPORTED"),
        ("/login/", "E_AUTH_REQUIRED"),
    ],
)
def test_share_resolution_refuses_media_group_and_login_before_import(
    tmp_path, monkeypatch, destination, code
):
    pages = {SHARE_PATH: (302, destination), destination: _synthetic_post_html()}
    with browser_fixture(tmp_path, monkeypatch, pages, url=SHARE) as env:
        ctx, project, permit, _, _, _, _ = env
        job = finish(ctx, submit(ctx, project, permit, url=SHARE))
        assert job["error"]["code"] == code
        if code == "E_UNSUPPORTED":
            assert "remote-media (B12)" in job["error"]["next_action"]
            assert "group post URLs are unsupported" in job["error"]["next_action"]
        with ctx.store.connection() as conn:
            assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
        assert job["limits"]["safe_read_retries"] == 0


@pytest.mark.parametrize("changed", [False, True])
def test_share_resume_requires_same_identity_and_retains_committed_items(
    tmp_path, monkeypatch, changed
):
    comments = [{"id": f"c_{i}", "text": f"Reliable {i}"} for i in range(60)]
    pages = {SHARE_PATH: (302, POST_PATH), POST_PATH: _synthetic_post_html(comments=comments)}
    with browser_fixture(tmp_path, monkeypatch, pages, url=SHARE) as env:
        ctx, project, permit, _, _, _, _ = env
        job_id = submit(ctx, project, permit, url=SHARE)
        saved = crash_after_batch(ctx, job_id)
        assert saved["resolved_permalink"] == URL
        if changed:
            other = "/other.actor/posts/OTHER_POST"
            pages[SHARE_PATH] = (302, other)
            pages[other] = _synthetic_post_html(actor="other.actor", post_id="OTHER_POST")
        job = finish(ctx, job_id)
        if changed:
            assert job["error"]["code"] == "E_DATA_STALE"
        else:
            assert job["error"] is None, job["error"]
            assert job["coverage_status"] == "scope_satisfied"
            assert job["result"]["data"]["steps"][0]["data"]["resolved_permalink"] == URL
        with ctx.store.connection() as conn:
            assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == (
                50 if changed else 61
            )
            assert (
                conn.execute("SELECT COUNT(*) FROM items WHERE native_id='OTHER_POST'").fetchone()[
                    0
                ]
                == 0
            )
            assert conn.execute("SELECT used FROM permits WHERE id=?", (permit,)).fetchone()[0] == 1
