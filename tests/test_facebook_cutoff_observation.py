"""A completed observation remains classifiable after the proxy itself cuts off."""

from browser_deadline import browser_deadline
from test_facebook import _synthetic_post_html
from test_facebook_review import browser_fixture, finish
from test_facebook_round5 import POST_PATH, rows, submit
from test_facebook_round6 import reset_seams  # noqa: F401


@browser_deadline
def test_r7_observed_login_survives_proxy_cutoff(tmp_path, monkeypatch):
    markup = _synthetic_post_html().replace("<body>", '<body><form hidden id="login_form"></form>')
    with browser_fixture(
        tmp_path, monkeypatch, {POST_PATH: markup}, unsandboxed=False, read_timeout=300
    ) as (
        ctx,
        project,
        permit,
        _,
        _,
        guard,
        driver,
    ):
        original = driver.open_session

        def opened(*args, **kwargs):
            session = original(*args, **kwargs)
            goto = session.goto_post

            def observed(url):
                result = goto(url)
                # Deterministically place a genuine proxy cutoff after a complete
                # DOM observation and before the application's destination recheck.
                assert not session._proxy._charge_bytes(2_000_000)
                assert session._proxy.byte_limit_exceeded and guard.byte_limit_exceeded
                return result

            session.goto_post = observed
            return session

        monkeypatch.setattr(driver, "open_session", opened)
        job = finish(ctx, submit(ctx, project, permit))
        assert job["error"] and job["error"]["code"] == "E_AUTH_REQUIRED", job
        assert job["state"] == "awaiting_user"
        assert rows(ctx) == []


def test_r7_cached_policy_recheck_cannot_be_cut_off():
    from dataclasses import replace

    import pytest

    from metamuse import _browser_egress, facebook
    from metamuse import models as m

    guard = facebook.EgressGuard(
        allowed_hosts=("www.facebook.com",), resolver=lambda _: ["93.184.216.34"]
    )
    url = "https://www.facebook.com/synth.lab/posts/pfbid0101synth"
    checked = guard._check_route_destination(url, "request")
    proxy = _browser_egress.BrowserEgressProxy(guard, max_bytes=1, start_closed=True)
    try:
        assert not proxy._charge_bytes(2)

        # No resolver may run again, even for a policy-only check after cutoff.
        def no_resolution(host):
            raise AssertionError("Pinned destination unexpectedly resolved again")

        guard.resolver = no_resolution
        guard.policy = replace(guard.policy, resolver=no_resolution)
        assert proxy.check_url_bounded(url, "request") == checked
        with pytest.raises(m.CoreError) as error:
            proxy.check_url_bounded("https://user:password@www.facebook.com/login/", "request")
        assert error.value.detail.code == m.ErrorCode.SCOPE_DENIED
        assert (
            error.value.detail.message == "Facebook browser destination is denied by egress policy"
        )
    finally:
        proxy.close()
