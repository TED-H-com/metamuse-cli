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
        "const p=window.open(''); if(p) new p.WebSocketStream('wss://'+location.host+'/escape');",
        "const p=window.open(''); if(p) {window.WebSocket._apiPassThrough(); "
        "new p.WebSocket('wss://'+location.host+'/escape');}",
        "const p=window.open(''); if(p) new p.WebSocket('wss://'+location.host+'/escape');",
        "const p=window.open(''); if(p) new p.Worker(p.URL.createObjectURL(new p.Blob(["
        "\"fetch('https://\"+location.host+\"/escape');\"],{type:'text/javascript'})));",
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
        assert not any(path.startswith("/escape") or upgrade for path, upgrade in hits)
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
