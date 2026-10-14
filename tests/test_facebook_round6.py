"""Round-6 regressions; synthetic fixtures and sandboxed loopback Chromium only."""

import pytest
from test_facebook import _synthetic_post_html
from test_facebook_review import browser_fixture, finish
from test_facebook_round5 import POST_PATH, fixture_job, submit

from metamuse import facebook


@pytest.fixture(autouse=True)
def reset_seams():
    yield
    facebook.set_driver_factory(None)
    facebook.set_egress_factory(None)
    facebook.set_secret_store_factory(None)
    facebook.set_resolver_factory(None)
    facebook.set_batch_hook(None)


@pytest.mark.parametrize(
    "marker,post,code",
    [
        ('<meta data-fb-state="login_wall">', True, "E_AUTH_REQUIRED"),
        ('<meta data-page-state="challenge">', True, "E_CHECKPOINT_REQUIRED"),
        ('<input type="hidden" data-checkpoint="true">', True, "E_CHECKPOINT_REQUIRED"),
        ('<head><meta data-fb-state="login_wall"></head>', False, "E_AUTH_REQUIRED"),
        ('<div hidden><form id="login_form"></form></div>', True, "E_AUTH_REQUIRED"),
        ('<form hidden id="checkpoint_Form"></form>', True, "E_CHECKPOINT_REQUIRED"),
    ],
    ids=[
        "meta-login",
        "meta-challenge",
        "hidden-input",
        "head-only",
        "login-form",
        "checkpoint-form",
    ],
)
def test_r6_barrier_markers_ignore_visibility(tmp_path, monkeypatch, marker, post, code):
    markup = (
        _synthetic_post_html().replace("<body>", "<body>" + marker)
        if post
        else ("<html>" + marker + "<body>Login</body></html>")
    )
    _, fixture = fixture_job(tmp_path / "fixture", markup)
    facebook.set_driver_factory(None)
    facebook.set_egress_factory(None)
    with browser_fixture(
        tmp_path / "chromium", monkeypatch, {POST_PATH: markup}, unsandboxed=False
    ) as (ctx, project, permit, *_):
        browser = finish(ctx, submit(ctx, project, permit))
    for job in (fixture, browser):
        assert job["error"] and job["error"]["code"] == code, job["error"]
        assert job["state"] == "awaiting_user"
        assert job["coverage_status"] != "scope_satisfied"


@pytest.mark.parametrize("hidden", ["hidden", 'aria-hidden="true"', 'style="display:none"'])
def test_r6_hidden_exhaustion_never_satisfies_scope(tmp_path, monkeypatch, hidden):
    markup = _synthetic_post_html(comments_exhausted=False).replace('data-comment-count="0"', "")
    markup = markup.replace(
        "</body>", f'<div {hidden} data-comments-exhausted="true"></div></body>'
    )
    _, fixture = fixture_job(tmp_path / "fixture", markup)
    facebook.set_driver_factory(None)
    facebook.set_egress_factory(None)
    with browser_fixture(
        tmp_path / "chromium", monkeypatch, {POST_PATH: markup}, unsandboxed=False
    ) as (ctx, project, permit, *_):
        browser = finish(ctx, submit(ctx, project, permit))
    for job in (fixture, browser):
        assert job["error"] is None
        assert job["coverage_status"] == "partial"


@pytest.mark.parametrize(
    "destination",
    [
        "https://denied.example/nav",
        "https://denied.example/login/",
        "https://denied.example/checkpoint/123/",
        "http://www.facebook.com/non_post_path",
        "https://user:password@www.facebook.com/login/",
        "https://www.facebook.com:444/checkpoint/",
        "https://127.0.0.1/login/",
    ],
)
def test_r6_destination_policy_precedes_classification(destination):
    from test_facebook_review import URL

    from metamuse import models as m

    guard = facebook.EgressGuard(
        allowed_hosts=("www.facebook.com",), resolver=lambda _: ["93.184.216.34"]
    )
    guard.authorize_permalink(facebook.parse_post_permalink(URL))
    with pytest.raises(m.CoreError) as error:
        guard.check_browser_route(destination, is_navigation=True, redirected_from=URL)
    assert error.value.detail.code == m.ErrorCode.SCOPE_DENIED
    assert error.value.detail.message == "Facebook browser destination is denied by egress policy"


def test_r6_denied_navigation_in_chromium(tmp_path, monkeypatch):
    markup = _synthetic_post_html().replace(
        "</body>", "<script>location.replace('https://denied.example/login/')</script></body>"
    )
    with browser_fixture(tmp_path, monkeypatch, {POST_PATH: markup}, unsandboxed=False) as (
        ctx,
        project,
        permit,
        _,
        hits,
        *_,
    ):
        job = finish(ctx, submit(ctx, project, permit))
        assert job["error"]["code"] == "E_SCOPE_DENIED"
        assert job["error"]["message"] == "Facebook browser destination is denied by egress policy"
        assert job["state"] == "awaiting_user"
        assert all(path == POST_PATH for path, _ in hits)
