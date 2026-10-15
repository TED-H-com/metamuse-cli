"""Popup creation controls in real sandboxed Chromium; loopback only."""

import pytest
from test_facebook import _synthetic_post_html
from test_facebook_review import browser_fixture, finish
from test_facebook_round5 import POST_PATH, submit
from test_facebook_round6 import reset_seams  # noqa: F401

from metamuse import facebook


@pytest.mark.parametrize(
    "script",
    [
        "const p=window.open(''); if(p) {new p.WebSocketStream('wss://'+location.host+'/escape');"
        "const until=Date.now()+250; while(Date.now()<until) {}}",
        "const p=window.open(''); if(p) {const ws=new p.WebSocket('wss://'+location.host+'/escape');"
        "if(ws._apiPassThrough) ws._apiPassThrough();"
        "const until=Date.now()+250; while(Date.now()<until) {}}",
        "const p=window.open(''); if(p) {new p.WebSocket('wss://'+location.host+'/escape');"
        "const until=Date.now()+250; while(Date.now()<until) {}}",
        "const p=window.open(''); if(p) new p.Worker(p.URL.createObjectURL(new p.Blob(["
        '"fetch(\'https://"+location.host+"/escape\');'
        "new WebSocketStream('wss://\"+location.host+\"/escape');\"],{type:'text/javascript'})));"
        "if(p) {const until=Date.now()+250; while(Date.now()<until) {}}",
        "window.open('/escape');",
        "const a=document.createElement('a'); a.href='/escape'; a.target='_blank'; "
        "document.body.append(a); a.click();",
        "const f=document.createElement('form'); f.action='/escape'; f.target='_blank'; "
        "document.body.append(f); f.submit();",
        "window.open('/escape','_blank','noopener');",
        "const f=document.createElement('iframe'); document.body.append(f); "
        "f.contentWindow.open('/escape');",
        "const p=window.open(''); if(p) p.open('/escape');",
    ],
    ids=[
        "stream",
        "passthrough",
        "plain",
        "blob-worker",
        "url",
        "blank-link",
        "blank-form",
        "noopener",
        "iframe",
        "nested-popup",
    ],
)
def test_r6_popup_creation_refused_and_counted(tmp_path, monkeypatch, script):
    # The pristine iframe open and synchronous popup code defeat an init-script-only fix.
    markup = _synthetic_post_html().replace(
        "</body>", '<img src="/hold"><script>' + script + "</script></body>"
    )
    with browser_fixture(
        tmp_path,
        monkeypatch,
        {POST_PATH: markup, "/hold": ""},
        delays={"/hold": 0.3},
        unsandboxed=False,
    ) as (ctx, project, permit, _, hits, guard, driver):
        original = driver.open_session
        sessions = []

        def opened(*args, **kwargs):
            session = original(*args, **kwargs)
            session._worker_cdp.send("Security.setIgnoreCertificateErrors", {"ignore": True})
            sessions.append(session)
            navigate = session.goto_post

            def goto(*args):
                result = navigate(*args)
                assert len(session._context.pages) == 1
                targets = session._worker_cdp.send("Target.getTargets")["targetInfos"]
                assert len([t for t in targets if t["type"] == "page"]) == 1
                return result

            monkeypatch.setattr(session, "goto_post", goto)
            return session

        monkeypatch.setattr(driver, "open_session", opened)
        job = finish(ctx, submit(ctx, project, permit))
        assert job["error"] is None, job["error"]
        assert not any(path.startswith("/escape") or upgrade for path, upgrade in hits), hits
        assert any(r["reason"] == "popup_blocked" for r in guard.summary()["aborted_reasons"])
        assert sessions[0].popups_blocked >= 1
        assert job["error"] is None, job["error"]
        assert job["state"] == "succeeded"


def test_r6_missing_popup_control_refuses_before_navigation(tmp_path, monkeypatch):
    with browser_fixture(
        tmp_path, monkeypatch, {POST_PATH: _synthetic_post_html()}, unsandboxed=False
    ) as (ctx, project, permit, _, hits, *_):
        original = facebook.build_chromium_launch_args
        monkeypatch.setattr(
            facebook,
            "build_chromium_launch_args",
            lambda port: [arg for arg in original(port) if arg != "--block-new-web-contents"],
        )
        job = finish(ctx, submit(ctx, project, permit))
        assert job["error"]["code"] == "E_DEPENDENCY_MISSING"
        assert job["error"]["message"] == "Facebook browser popup creation control is unavailable"
        assert not hits


def test_r7_iframe_only_popup_control_failure(tmp_path, monkeypatch):
    # Real sandboxed blank Chromium; expose only the pristine iframe escape.
    # The top-level native open remains refused and no navigation is performed.
    with browser_fixture(
        tmp_path, monkeypatch, {POST_PATH: _synthetic_post_html()}, unsandboxed=False
    ) as (ctx, project, permit, _, hits, *_):
        verify = facebook._verify_popup_control

        def iframe_escape(browser, context, page):
            # Same isolated-world blank probe: top-level native open is refused,
            # while only the iframe branch is made to return a live Window.
            from metamuse import _facebook_dom

            source = _facebook_dom.POPUP_CHECK
            with monkeypatch.context() as patch:
                patch.setattr(
                    _facebook_dom,
                    "POPUP_CHECK",
                    source.replace(
                        "const n_window=apply(v_open,n_fresh,['']);", "const n_window=n_fresh;"
                    ),
                )
                return verify(browser, context, page)

        monkeypatch.setattr(facebook, "_verify_popup_control", iframe_escape)
        job = finish(ctx, submit(ctx, project, permit))
        assert job["error"] and job["error"]["code"] == "E_DEPENDENCY_MISSING", job
        assert job["error"]["message"] == "Facebook browser popup creation control is unavailable"
        assert not hits
