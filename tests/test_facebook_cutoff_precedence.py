"""A byte cutoff cannot downgrade policy, barrier or guard failures."""

import threading

import pytest
from browser_deadline import browser_deadline
from test_facebook import _setup_fb_env, _synthetic_post_html
from test_facebook_review import browser_fixture, finish, observation
from test_facebook_round5 import POST_PATH, rows, submit
from test_facebook_round6 import reset_seams  # noqa: F401

from metamuse import facebook
from metamuse import models as m


@pytest.mark.parametrize(
    "code",
    [
        m.ErrorCode.SCOPE_DENIED,
        m.ErrorCode.AUTH_REQUIRED,
        m.ErrorCode.CHECKPOINT_REQUIRED,
        m.ErrorCode.RATE_LIMITED,
        m.ErrorCode.PROTOCOL_MISMATCH,
        m.ErrorCode.REFERENCE_INVALID,
    ],
)
@pytest.mark.parametrize("cutoff", [False, True])
def test_r7_fatal_stop_identical_with_cutoff(tmp_path, code, cutoff):
    ctx, project, permit, _, _ = _setup_fb_env(tmp_path)
    markup = _synthetic_post_html(
        comments=[{"id": "c1", "text": "First"}], expand_controls=[{"id": "more"}]
    )

    class Driver(facebook.FixtureBrowserDriver):
        def open_session(self, storage_state, egress, **kwargs):
            session = super().open_session(storage_state, egress, **kwargs)

            def expand(control):
                if cutoff:
                    egress.mark_byte_limit_exceeded()
                raise m.CoreError(
                    code, "Synthetic fatal stop", next_action="Synthetic operator action"
                )

            session.expand_next = expand
            return session

    facebook.set_driver_factory(lambda: Driver(observation(markup)))
    job = finish(ctx, submit(ctx, project, permit))
    assert job["error"] and job["error"]["code"] == code.value, job
    assert job["error"]["message"] == "Synthetic fatal stop"
    assert job["error"]["next_action"] == "Synthetic operator action"
    assert job["state"] == (
        "awaiting_user"
        if code
        in {m.ErrorCode.SCOPE_DENIED, m.ErrorCode.AUTH_REQUIRED, m.ErrorCode.CHECKPOINT_REQUIRED}
        else "failed"
    )
    assert rows(ctx) == []


@pytest.mark.parametrize("real_browser", [False, True], ids=["fixture", "chromium"])
def test_r7_login_observation_precedes_byte_budget(tmp_path, monkeypatch, real_browser):
    markup = _synthetic_post_html(post_text="X" * 50_000).replace(
        "<body>", '<body><form hidden id="login_form"></form>'
    )
    if real_browser:
        # Return the real, complete Chromium observation, then latch the byte cutoff.
        # This fixes the concurrency order deterministically without relying on a
        # streaming parser receiving a particular TLS chunk before socket closure.
        with browser_fixture(tmp_path, monkeypatch, {POST_PATH: markup}, unsandboxed=False) as (
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
                    guard.mark_byte_limit_exceeded()
                    return result

                session.goto_post = observed
                return session

            monkeypatch.setattr(driver, "open_session", opened)
            job = finish(ctx, submit(ctx, project, permit))
    else:
        ctx, project, permit, _, _ = _setup_fb_env(tmp_path)
        facebook.set_driver_factory(lambda: facebook.FixtureBrowserDriver(observation(markup)))
        job = finish(ctx, submit(ctx, project, permit, max_bytes=24_000))
    assert job["error"] and job["error"]["code"] == "E_AUTH_REQUIRED", job
    assert job["state"] == "awaiting_user"
    assert rows(ctx) == []


@pytest.mark.parametrize("where", ["goto", "close", "latched-guard"])
def test_r7_guard_verified_after_cutoff(tmp_path, monkeypatch, where):
    with browser_fixture(
        tmp_path, monkeypatch, {POST_PATH: _synthetic_post_html()}, unsandboxed=False
    ) as (_, _, _, _, _, guard, driver):
        session = driver.open_session(
            facebook.SanitizedStorageState(
                cookies=[], origins=[], secret_tokens=(), cookie_hosts=(), dropped_cookie_domains=()
            ),
            guard,
            timeout_ms=300_000,
            max_bytes=1_000_000,
        )
        target = session._network_cdp.send("Target.getTargetInfo")["targetInfo"]["targetId"]
        guard.mark_byte_limit_exceeded()
        if where == "latched-guard":
            session._guard_failed()
        else:
            session._closing_targets.add(target)
            # Preserve a listed target even after the byte watcher closes the main
            # page. Unknown foreign-target liveness must still be verified.
            monkeypatch.setattr(session, "_target_gone", lambda _: False)
        try:
            with pytest.raises(m.CoreError) as error:
                if where == "close":
                    session.close()
                else:
                    session.goto_post(POST_PATH)
            assert error.value.detail.code == m.ErrorCode.PROTOCOL_MISMATCH
        finally:
            if where != "close":
                try:
                    session.close()
                except m.CoreError as exc:
                    assert exc.detail.code == m.ErrorCode.PROTOCOL_MISMATCH


def test_r7_denied_expansion_navigation_precedes_cutoff(tmp_path, monkeypatch):
    markup = _synthetic_post_html(expand_controls=[{"id": "more"}]).replace(
        "</body>",
        "<script>document.querySelector('button').onclick=()=>"
        "location.replace('https://denied.example/login/');</script></body>",
    )
    with browser_fixture(tmp_path, monkeypatch, {POST_PATH: markup}, unsandboxed=False) as (
        ctx,
        project,
        permit,
        _,
        hits,
        guard,
        _,
    ):
        check = guard.check_browser_route

        def checked(url, **kwargs):
            try:
                return check(url, **kwargs)
            except m.CoreError as exc:
                if exc.detail.code == m.ErrorCode.SCOPE_DENIED:
                    guard.mark_byte_limit_exceeded()
                raise

        monkeypatch.setattr(guard, "check_browser_route", checked)
        job = finish(ctx, submit(ctx, project, permit))
        assert job["error"] and job["error"]["code"] == "E_SCOPE_DENIED", job
        assert job["state"] == "awaiting_user"
        assert rows(ctx) == []
        assert all(path == POST_PATH for path, _ in hits)


@pytest.mark.parametrize(
    "winner,other",
    [
        ("E_SCOPE_DENIED", "E_AUTH_REQUIRED"),
        ("E_AUTH_REQUIRED", "E_REFERENCE_INVALID"),
        ("E_CHECKPOINT_REQUIRED", "E_REFERENCE_INVALID"),
        ("E_AUTH_REQUIRED", "E_CHECKPOINT_REQUIRED"),
    ],
)
def test_r7_simultaneous_fatal_precedence(winner, other):
    session = object.__new__(facebook._PlaywrightSession)
    session._closing_targets = set()
    session._transport_error = None
    session._proxy = None
    session._navigation_urls = []
    session._navigation_responded = set()
    session._document_status = 200
    session._peer_error = m.CoreError(m.ErrorCode(winner), "Synthetic winning stop")
    session._egress = facebook.EgressGuard(
        allowed_hosts=("www.facebook.com",), resolver=lambda _: ["93.184.216.34"]
    )
    other_message = (
        "Facebook navigation left the approved post"
        if other == "E_CHECKPOINT_REQUIRED"
        else "Other stop"
    )
    session._egress.navigation_error = m.CoreError(m.ErrorCode(other), other_message)
    session._egress.byte_limit_exceeded = True
    with pytest.raises(m.CoreError) as error:
        session._raise_if_byte_limit()
    assert error.value.detail.code.value == winner


@pytest.mark.parametrize(
    "status,code", [(401, "E_AUTH_REQUIRED"), (429, "E_RATE_LIMITED"), (500, "E_PROTOCOL_MISMATCH")]
)
@browser_deadline
def test_r7_observed_http_barrier_precedes_cutoff(tmp_path, monkeypatch, status, code, request):
    headers_seen = threading.Event()

    def oversize(request):
        request.send_response(status)
        request.send_header("Content-Type", "text/html")
        request.send_header("Content-Length", "100000")
        request.send_header("X-Content-Type-Options", "nosniff")
        request.end_headers()
        try:
            prefix = b"<html><body>" + b"X" * 1024
            request.wfile.write(prefix)
            request.wfile.flush()
            headers_seen.wait(timeout=600)
            request.wfile.write(b"X" * (100000 - len(prefix)))
        except OSError:
            pass

    with browser_fixture(tmp_path, monkeypatch, {POST_PATH: oversize}, unsandboxed=False) as (
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
            session._page.on(
                "response",
                lambda response: headers_seen.set() if response.status == status else None,
            )
            return session

        monkeypatch.setattr(driver, "open_session", opened)
        try:
            job = finish(ctx, submit(ctx, project, permit, max_bytes=24000))
        finally:
            headers_seen.set()
        assert job["error"] and job["error"]["code"] == code, job
        assert job["state"] == ("awaiting_user" if status == 401 else "failed")
        assert guard.byte_limit_exceeded
        assert rows(ctx) == []
