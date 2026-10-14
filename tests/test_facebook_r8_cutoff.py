"""Only byte-cutoff interruptions may lose to the byte budget."""

# ruff: noqa: E501

import pytest
from test_facebook import _setup_fb_env, _synthetic_post_html
from test_facebook_review import URL, browser_fixture, finish, observation
from test_facebook_round5 import POST_PATH, rows, submit
from test_facebook_round6 import reset_seams  # noqa: F401

from metamuse import facebook
from metamuse import models as m
from metamuse._browser_egress import BrowserEgressProxy


def session_for(guard):
    session = object.__new__(facebook._PlaywrightSession)
    session._egress = guard
    session._closing_targets = set()
    session._transport_error = session._peer_error = session._proxy = None
    session._navigation_urls = []
    session._navigation_responded = set()
    session._document_status = 200
    return session


@pytest.mark.parametrize(
    "code",
    [
        "E_SCOPE_DENIED",
        "E_AUTH_REQUIRED",
        "E_CHECKPOINT_REQUIRED",
        "E_RATE_LIMITED",
        "E_REFERENCE_INVALID",
        "E_UNSUPPORTED",
        "E_DATA_STALE",
        "E_PROTOCOL_MISMATCH",
    ],
)
@pytest.mark.parametrize("order", ["none", "cutoff-first", "fatal-first"])
def test_r8_fatal_cutoff_outcome_matches_control(tmp_path, code, order):
    ctx, project, permit, _, _ = _setup_fb_env(tmp_path)

    class Driver(facebook.FixtureBrowserDriver):
        def open_session(self, state, guard, **kwargs):
            fixture = super().open_session(state, guard, **kwargs)
            session = session_for(guard)

            def expand(control):
                if order == "cutoff-first":
                    guard.mark_byte_limit_exceeded()
                error = m.CoreError(
                    m.ErrorCode(code), "Synthetic fatal condition", next_action="Synthetic action"
                )
                if code == "E_PROTOCOL_MISMATCH":
                    session._transport_error = error
                else:
                    guard.navigation_error = error
                if order == "fatal-first":
                    guard.mark_byte_limit_exceeded()
                session._raise_if_byte_limit()

            fixture.expand_next = expand
            return fixture

    facebook.set_driver_factory(
        lambda: Driver(observation(_synthetic_post_html(expand_controls=[{"id": "more"}])))
    )
    job = finish(ctx, submit(ctx, project, permit))
    assert job["error"]["code"] == code, job
    assert job["error"]["next_action"] == "Synthetic action"
    assert job["state"] == (
        "awaiting_user"
        if code in {"E_SCOPE_DENIED", "E_AUTH_REQUIRED", "E_CHECKPOINT_REQUIRED", "E_DATA_STALE"}
        else "failed"
    )
    assert rows(ctx) == []


@pytest.mark.parametrize(
    "message",
    [
        "Browser navigation to Facebook post permalink failed",
        "Facebook browser socket closed during byte cutoff",
        "Facebook browser wait interrupted by byte cutoff",
    ],
)
def test_r8_named_cutoff_interruptions(message):
    guard = facebook.EgressGuard(
        allowed_hosts=("www.facebook.com",), resolver=lambda _: ["93.184.216.34"]
    )
    session = session_for(guard)
    guard.byte_limit_exceeded = True
    guard.navigation_error = m.CoreError(m.ErrorCode.NETWORK_TIMEOUT, message)
    with pytest.raises(m.CoreError) as caught:
        session._raise_if_byte_limit()
    assert caught.value.detail.code == m.ErrorCode.LIMIT_REACHED


@pytest.mark.parametrize("purpose", ["request", "redirect"])
@pytest.mark.parametrize("pinned", [False, True])
@pytest.mark.parametrize(
    "destination,code",
    [
        ("https://denied.example/login/", "E_SCOPE_DENIED"),
        ("https://user:pw@m.facebook.com/x", "E_SCOPE_DENIED"),
        ("http://m.facebook.com/watch/", "E_SCOPE_DENIED"),
        ("https://m.facebook.com:8443/x", "E_SCOPE_DENIED"),
        ("https://127.0.0.1/x", "E_SCOPE_DENIED"),
        ("https://m.facebook.com/x%0d%0a", "E_SCOPE_DENIED"),
        ("https://m.facebook.com/login.php", "E_AUTH_REQUIRED"),
        ("https://m.facebook.com/checkpoint/x", "E_CHECKPOINT_REQUIRED"),
    ],
)
def test_r8_dns_free_policy_before_latch(purpose, pinned, destination, code):
    calls = []
    guard = facebook.EgressGuard(
        allowed_hosts=("www.facebook.com", "m.facebook.com"),
        resolver=lambda host: calls.append(host) or ["93.184.216.34"],
    )
    proxy = BrowserEgressProxy(guard, max_bytes=10, start_closed=True)
    try:
        if pinned:
            proxy.check_url_bounded(
                "https://m.facebook.com/synth.lab/posts/pfbid0101synth", purpose
            )
        before = len(calls)
        proxy._charge_bytes(100)
        with pytest.raises(m.CoreError) as caught:
            proxy.check_url_bounded(destination, purpose)
        assert caught.value.detail.code.value == code
        assert len(calls) == before
    finally:
        proxy.close()


@pytest.mark.parametrize("site", ["route", "proxy", "protocol"])
def test_r8_recorded_fatal_not_overwritten(site):
    guard = facebook.EgressGuard(
        allowed_hosts=("www.facebook.com",), resolver=lambda _: ["93.184.216.34"]
    )
    denial = m.CoreError(m.ErrorCode.SCOPE_DENIED, "First denial")
    guard.navigation_error = denial
    guard.last_proxy_error = denial
    guard.proxy_errors_by_authority[("www.facebook.com", 443)] = denial
    if site == "route":
        guard._route_url_checker = lambda *a: (_ for _ in ()).throw(
            m.CoreError(m.ErrorCode.LIMIT_REACHED, "Cutoff")
        )
        with pytest.raises(m.CoreError):
            guard.check_browser_route(URL, is_navigation=True)
    elif site == "proxy":
        guard._checked_destination = lambda *a: (_ for _ in ()).throw(
            m.CoreError(m.ErrorCode.NETWORK_TIMEOUT, "Temporary DNS")
        )
        with pytest.raises(m.CoreError):
            guard.check_proxy_target(URL, method="CONNECT", is_connect=True, is_navigation=True)
    else:
        guard.record_proxy_protocol_refusal(
            hostname="www.facebook.com", reason="resolution_timeout", url=URL
        )
    assert guard.navigation_error is denial
    assert guard.last_proxy_error is denial
    assert guard.proxy_errors_by_authority[("www.facebook.com", 443)] is denial


@pytest.mark.parametrize("order", ["none", "cutoff-first", "fatal-first"])
def test_r8_denial_after_final_observation(tmp_path, monkeypatch, order):
    with browser_fixture(
        tmp_path, monkeypatch, {POST_PATH: _synthetic_post_html()}, unsandboxed=False
    ) as (ctx, project, permit, _, _, guard, driver):
        original = driver.open_session

        def opened(*a, **k):
            session = original(*a, **k)
            goto = session.goto_post

            def observed(url):
                result = goto(url)
                if order == "cutoff-first":
                    guard.mark_byte_limit_exceeded()
                try:
                    guard.check_browser_route("https://denied.example/login/", is_navigation=True)
                except m.CoreError:
                    pass
                if order == "fatal-first":
                    guard.mark_byte_limit_exceeded()
                return result

            session.goto_post = observed
            return session

        monkeypatch.setattr(driver, "open_session", opened)
        job = finish(ctx, submit(ctx, project, permit))
        assert job["error"]["code"] == "E_SCOPE_DENIED", job
        assert job["state"] == "awaiting_user"
        assert rows(ctx) == []


@pytest.mark.parametrize(
    "condition",
    [
        "guard",
        "denial",
        "login",
        "checkpoint",
        "challenge",
        "rate",
        "offpost",
        "share-nonpost",
        "share-stale",
    ],
)
def test_r8_chromium_fatal_cutoff_pairs(tmp_path, monkeypatch, condition):
    import json

    codes = {
        "guard": "E_PROTOCOL_MISMATCH",
        "denial": "E_SCOPE_DENIED",
        "login": "E_AUTH_REQUIRED",
        "checkpoint": "E_CHECKPOINT_REQUIRED",
        "challenge": "E_CHECKPOINT_REQUIRED",
        "rate": "E_RATE_LIMITED",
        "offpost": "E_REFERENCE_INVALID",
        "share-nonpost": "E_UNSUPPORTED",
        "share-stale": "E_DATA_STALE",
    }
    destinations = {
        "denial": "https://denied.example/login/",
        "login": "/login/",
        "checkpoint": "/checkpoint/x",
        "offpost": "/other.actor/posts/OTHER",
    }
    baseline = None
    for order in ["none", "cutoff-first", "fatal-first"]:
        with monkeypatch.context() as patch:
            markup = _synthetic_post_html(expand_controls=[{"id": "more"}])
            if condition in destinations:
                markup = markup.replace(
                    "</body>",
                    '<script>document.querySelector("button").onclick=()=>location.replace('
                    + json.dumps(destinations[condition])
                    + ");</script></body>",
                )
            if condition in {"challenge", "rate"}:
                state = "challenge" if condition == "challenge" else "rate_limited"
                markup = markup.replace(
                    "</body>",
                    '<script>document.querySelector("button").onclick=()=>{document.body.setAttribute("data-page-state",'
                    + json.dumps(state)
                    + ');document.querySelector("button").remove();};</script></body>',
                )
            url = URL
            pages = {POST_PATH: markup}
            if condition.startswith("share-"):
                url = "https://www.facebook.com/share/p/SyntheticCode1/"
                destination = (
                    "/reel/synthetic/"
                    if condition == "share-nonpost"
                    else "/other.actor/posts/OTHER"
                )
                pages = {
                    "/share/p/SyntheticCode1/": (302, destination),
                    destination: _synthetic_post_html(),
                }
            with browser_fixture(tmp_path / order, patch, pages, url=url, unsandboxed=False) as (
                ctx,
                project,
                permit,
                _,
                _,
                guard,
                driver,
            ):
                original = driver.open_session
                check = guard.check_browser_route
                classify = facebook.classify_and_parse_observation
                sessions = []

                def latch(sessions=sessions, guard=guard):
                    # Actual proxy cutoff closes its sockets; the guard marker is
                    # also read by deterministic post-observation ordering cases.
                    if sessions:
                        sessions[-1]._proxy._charge_bytes(10**9)
                    else:
                        guard.mark_byte_limit_exceeded()

                def opened(*a, original=original, sessions=sessions, order=order, **k):
                    session = original(*a, **k)
                    sessions.append(session)
                    if condition == "share-stale":
                        guard.resume_identity = facebook.parse_post_permalink(URL)
                    if condition == "guard":
                        expand = session.expand_next

                        def guarded(control):
                            if order == "cutoff-first":
                                latch()
                            session._guard_failed()
                            if order == "fatal-first":
                                latch()
                            return expand(control)

                        session.expand_next = guarded
                    return session

                def checked(destination, check=check, order=order, **kwargs):
                    trigger = kwargs.get("is_navigation") and (
                        (
                            condition in destinations
                            and (
                                destination.endswith(destinations[condition])
                                if destinations[condition].startswith("/")
                                else destination == destinations[condition]
                            )
                        )
                        or (condition.startswith("share-") and "/share/" not in destination)
                    )
                    if trigger and order == "cutoff-first":
                        latch()
                    try:
                        return check(destination, **kwargs)
                    except m.CoreError:
                        if trigger and order == "fatal-first":
                            latch()
                        raise

                def classified(obs, classify=classify, order=order, **kwargs):
                    trigger = condition in {"challenge", "rate"} and any(
                        n["attrs"].get("data-page-state") in {"challenge", "rate_limited"}
                        for n in getattr(obs, "capture_nodes", None) or []
                    )
                    if (
                        condition in {"challenge", "rate"}
                        and getattr(obs, "capture_nodes", None) is None
                    ):
                        state = "challenge" if condition == "challenge" else "rate_limited"
                        trigger = f'data-page-state="{state}"' in obs.html
                    if trigger and order == "cutoff-first":
                        latch()
                    try:
                        return classify(obs, **kwargs)
                    except m.CoreError:
                        if trigger and order == "fatal-first":
                            latch()
                        raise

                patch.setattr(driver, "open_session", opened)
                patch.setattr(guard, "check_browser_route", checked)
                patch.setattr(facebook, "classify_and_parse_observation", classified)
                job = finish(ctx, submit(ctx, project, permit, url=url))
                assert job["error"]["code"] == codes[condition], (order, condition, job)
                outcome = (job["state"], job["error"], rows(ctx))
                if order == "none":
                    baseline = outcome
                assert outcome == baseline, (order, outcome, baseline)
