"""Guard fault injection around real sandboxed Chromium and synthetic loopback pages."""

import pytest
from test_facebook import _synthetic_post_html
from test_facebook_review import browser_fixture, finish
from test_facebook_round5 import POST_PATH, submit
from test_facebook_round6 import reset_seams  # noqa: F401


@pytest.mark.parametrize("failure", ["false", "exception", "unpaused"])
def test_r6_worker_shutdown_guard_fails_closed(tmp_path, monkeypatch, failure):
    markup = _synthetic_post_html().replace(
        "</body>",
        """<script>
      new Worker(URL.createObjectURL(new Blob(["fetch('/escape')"], {type:'text/javascript'})));
      </script></body>""",
    )
    with browser_fixture(tmp_path, monkeypatch, {POST_PATH: markup}, unsandboxed=False) as (
        ctx,
        project,
        permit,
        _,
        hits,
        _,
        driver,
    ):
        original = driver.open_session

        def opened(*args, **kwargs):
            session = original(*args, **kwargs)
            send = session._worker_cdp.send

            def broken(method, params=None):
                if method == "Target.closeTarget":
                    if failure == "false":
                        return {"success": False}
                    if failure == "exception":
                        raise RuntimeError("synthetic close failure")
                return send(method, params)

            monkeypatch.setattr(session._worker_cdp, "send", broken)
            if failure != "unpaused":
                make_channel = session._browser.new_browser_cdp_session

                def no_shutdown_channel():
                    channel = make_channel()
                    channel_send = channel.send

                    def dropped(method, params=None):
                        if method == "Target.sendMessageToTarget":
                            return {}  # Sabotage the fixed paused-worker shutdown too.
                        return channel_send(method, params)

                    monkeypatch.setattr(channel, "send", dropped)
                    return channel

                monkeypatch.setattr(
                    session._browser, "new_browser_cdp_session", no_shutdown_channel
                )
            if failure == "unpaused":
                # Inject a false pause report on the real worker's attachment event.
                emitter = session._worker_cdp._impl_obj
                emit = emitter.emit

                def unpaused(event, *args):
                    if event == "Target.attachedToTarget":
                        args[0]["waitingForDebugger"] = False
                    return emit(event, *args)

                monkeypatch.setattr(emitter, "emit", unpaused)
            return session

        monkeypatch.setattr(driver, "open_session", opened)
        job = finish(ctx, submit(ctx, project, permit))
        assert job["error"] and job["error"]["code"] == "E_PROTOCOL_MISMATCH", job["error"]
        assert job["error"]["message"] == "Facebook browser guard could not verify target shutdown"
        assert job["state"] == "failed"
        assert not any(path == "/escape" for path, _ in hits)


@pytest.mark.parametrize("gone", [False, True])
def test_r6_popup_close_exception_checks_liveness(tmp_path, monkeypatch, gone):
    from playwright.sync_api import Page

    with browser_fixture(
        tmp_path, monkeypatch, {POST_PATH: _synthetic_post_html()}, unsandboxed=False
    ) as (ctx, project, permit, _, _, _, driver):
        original = driver.open_session
        close = Page.close

        def opened(*args, **kwargs):
            session = original(*args, **kwargs)

            def broken(page, *args, **kwargs):
                if page is session._page:
                    return close(page, *args, **kwargs)
                if gone:
                    close(page, *args, **kwargs)
                raise RuntimeError("synthetic popup close failure")

            monkeypatch.setattr(Page, "close", broken)
            if hasattr(session, "_page_target_cdp"):
                session._page_target_cdp.send(
                    "Target.setAutoAttach",
                    {
                        "autoAttach": False,
                        "waitForDebuggerOnStart": False,
                        "flatten": True,
                    },
                )
            # Browser-protocol creation bypasses the renderer switch and exercises
            # the independent context page handler against a real foreign target.
            session._context.new_page()
            return session

        monkeypatch.setattr(driver, "open_session", opened)
        job = finish(ctx, submit(ctx, project, permit))
        if gone:
            assert job["error"] is None
            assert job["state"] == "succeeded"
        else:
            assert job["error"] and job["error"]["code"] == "E_PROTOCOL_MISMATCH", job["error"]
            assert job["state"] == "failed"
